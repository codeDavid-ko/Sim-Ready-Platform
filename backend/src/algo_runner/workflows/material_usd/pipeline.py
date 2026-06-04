"""pipeline.py — material-usd 3단계(ingest / classify / build) 함수.

레퍼런스 스크립트(ingest.py/classify.py/build_usd.py)를 플랫폼에서 직접 호출 가능한
순수 함수로 옮긴 것. 파일은 바이트로 받아 임시파일에 쓰고 common 으로 처리한다.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import trimesh

from . import common

_ASSETS = Path(__file__).parent / "assets"
_CATALOG = _ASSETS / "vmaterials_catalog.json"
_PROMPT = _ASSETS / "classify_prompt.md"
_SUBSTATION = _ASSETS / "substation_palette.json"

_SUPPORTED_EXT = {".stl", ".step", ".stp", ".usd", ".usda", ".usdc", ".usdz", ".glb", ".gltf", ".obj", ".ply"}


def _write_temp(file_bytes: bytes, file_name: str) -> str:
    ext = os.path.splitext(file_name or "")[1].lower() or ".stl"
    fd, path = tempfile.mkstemp(suffix=ext)
    with os.fdopen(fd, "wb") as f:
        f.write(file_bytes)
    return path


def _load_normalized(file_bytes: bytes, file_name: str, in_units: str, up_axis: str):
    path = _write_temp(file_bytes, file_name)
    try:
        raw = common.load_parts(path)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return [(n, common.normalize(V, in_units, up_axis), F) for n, V, F in raw]


# ---------- [1] ingest ----------
def ingest(file_bytes: bytes, file_name: str, in_units: str, up_axis: str) -> tuple[dict[str, Any], bytes]:
    """형상 → (parts.json dict, 뷰어용 GLB 바이트)."""
    parts = _load_normalized(file_bytes, file_name, in_units, up_axis)
    parts_json = {
        "input": file_name,
        "in_units": in_units,
        "up_axis": up_axis,
        "part_count": len(parts),
        "parts": common.part_summary(parts),
    }
    # 뷰어용 GLB (정규화된 좌표)
    scene = trimesh.Scene()
    for n, V, F in parts:
        scene.add_geometry(trimesh.Trimesh(vertices=V, faces=F), node_name=n)
    glb_bytes = scene.export(file_type="glb")
    return parts_json, glb_bytes


# ---------- [2] classify ----------
def _compact_catalog(limit: int = 400) -> list[dict[str, Any]]:
    cat = json.loads(_CATALOG.read_text(encoding="utf-8"))
    rows = []
    for e in cat:
        rows.append(
            {
                "subId": e["subId"],
                "mdl": e["module"],
                "category": e.get("category"),
                "color": e.get("thumb_color") or e.get("color_linear"),
            }
        )
    return rows[:limit]


def _default_assignment(parts_json: dict[str, Any], vmat_root: str) -> dict[str, Any]:
    """LLM 키가 없을 때 폴백 — 변전소 기본 팔레트의 cabinet 재질을 전 부품에 적용."""
    sub = json.loads(_SUBSTATION.read_text(encoding="utf-8"))
    spec = dict(sub.get("cabinet", {"mdl": "Metal/Steel_Painted.mdl", "subId": "Steel_Painted", "inputs": {}}))
    spec["vmat_root"] = vmat_root
    part_map = {p["name"]: "default" for p in parts_json.get("parts", [])}
    part_map["__default__"] = "default"
    return {
        "parts": part_map,
        "palette": {"default": spec},
        "notes": "LLM 미사용(API 키 없음): 기본 팔레트(cabinet) 적용. 키를 넣으면 이미지/설명 기반 분류가 활성화됩니다.",
    }


def classify(
    parts_json: dict[str, Any],
    mode: str,
    text: str,
    images: list[tuple[bytes, str]],
    vmat_root: str,
    api_key: str = "",
    model: str = "claude-sonnet-4-6",
) -> dict[str, Any]:
    """참조(이미지/설명) → assignment.json dict. 키 없으면 기본 팔레트 폴백."""
    parts = parts_json.get("parts", [])
    if not api_key:
        return _default_assignment(parts_json, vmat_root)

    template = _PROMPT.read_text(encoding="utf-8")
    prompt = template.format(
        mode=mode,
        parts_json=json.dumps(parts, ensure_ascii=False, indent=2),
        user_text=text or "(없음)",
        catalog_json=json.dumps(_compact_catalog(), ensure_ascii=False),
    )

    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for data, mime in images:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime or "image/jpeg",
                    "data": base64.standard_b64encode(data).decode(),
                },
            }
        )
    msg = client.messages.create(
        model=model, max_tokens=4000, messages=[{"role": "user", "content": content}]
    )
    raw = "".join(b.text for b in msg.content if b.type == "text")
    raw = raw[raw.find("{") : raw.rfind("}") + 1]  # JSON만 추출
    asg = json.loads(raw)
    for _k, spec in asg.get("palette", {}).items():
        spec.setdefault("vmat_root", vmat_root)
    return asg


# ---------- [3] build ----------
def build(
    file_bytes: bytes,
    file_name: str,
    in_units: str,
    up_axis: str,
    assignment: dict[str, Any],
) -> tuple[bytes, dict[str, Any]]:
    """원본 형상 + assignment → 재질 바인딩 자기완결 USDA 바이트 + info."""
    parts = _load_normalized(file_bytes, file_name, in_units, up_axis)
    usda, info = common.write_usd_string(parts, assignment["parts"], assignment["palette"])
    return usda.encode("utf-8"), info
