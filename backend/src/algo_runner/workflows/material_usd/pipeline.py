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
def _compact_catalog(limit: int | None = None) -> list[dict[str, Any]]:
    """LLM에 줄 재질 어휘. limit=None 이면 전체(2596종) 사용."""
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
    return rows if limit is None else rows[:limit]


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


def _build_prompt(parts: list[dict[str, Any]], mode: str, text: str) -> str:
    template = _PROMPT.read_text(encoding="utf-8")
    return template.format(
        mode=mode,
        parts_json=json.dumps(parts, ensure_ascii=False, indent=2),
        user_text=text or "(없음)",
        catalog_json=json.dumps(_compact_catalog(), ensure_ascii=False),
    )


def _parse_assignment(raw_text: str, vmat_root: str) -> dict[str, Any]:
    raw = raw_text[raw_text.find("{") : raw_text.rfind("}") + 1]  # JSON만 추출
    asg = json.loads(raw)
    for _k, spec in asg.get("palette", {}).items():
        spec.setdefault("vmat_root", vmat_root)
    return asg


_IMG_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


async def _classify_via_agent(prompt: str, images: list[tuple[bytes, str]], model: str) -> str:
    """구독(claude-agent-sdk) 경로 — 이미지는 임시파일로 저장해 에이전트가 Read 한다.
    cwd 는 주지 않고 절대경로만 사용(Windows cwd 슬러그 함정 회피)."""
    from claude_agent_sdk import ClaudeAgentOptions, query

    from ...llm import _sync_env

    _sync_env()  # settings → os.environ (CLAUDE_CODE_OAUTH_TOKEN 등을 SDK가 읽게)
    paths: list[str] = []
    try:
        for data, mime in images:
            fd, p = tempfile.mkstemp(suffix=_IMG_EXT.get(mime, ".jpg"))
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            paths.append(p)
        full = prompt
        if paths:
            full += "\n\n## 참조 이미지 (Read 도구로 각 파일을 읽어 실제 색을 확인)\n" + "\n".join(paths)
        opts = ClaudeAgentOptions(
            model=model, allowed_tools=["Read"], permission_mode="bypassPermissions"
        )
        out: list[str] = []
        async for m in query(prompt=full, options=opts):
            for b in getattr(m, "content", None) or []:
                t = getattr(b, "text", None)
                if t:
                    out.append(t)
        return "".join(out)
    finally:
        for p in paths:
            try:
                os.remove(p)
            except OSError:
                pass


def _classify_via_anthropic(prompt: str, images: list[tuple[bytes, str]], api_key: str) -> str:
    """raw anthropic(API 키) 경로 — 비전 네이티브. 종량 과금 fallback."""
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
        model="claude-sonnet-4-6", max_tokens=4000, messages=[{"role": "user", "content": content}]
    )
    return "".join(b.text for b in msg.content if b.type == "text")


async def classify(
    parts_json: dict[str, Any],
    mode: str,
    text: str,
    images: list[tuple[bytes, str]],
    vmat_root: str,
    api_key: str = "",
    oauth_token: str = "",
    model: str = "claude-sonnet-4-6",
) -> dict[str, Any]:
    """참조(이미지/설명) → assignment dict.

    우선순위: API 키(raw anthropic, 비전 네이티브 — 종량 과금) > 구독 OAuth(claude-agent-sdk,
    이미지는 에이전트가 파일 Read) > 둘 다 없으면 기본 팔레트 폴백.
    """
    parts = parts_json.get("parts", [])
    if not (api_key or oauth_token):
        return _default_assignment(parts_json, vmat_root)
    prompt = _build_prompt(parts, mode, text)
    if api_key:
        raw = _classify_via_anthropic(prompt, images, api_key)
    else:
        raw = await _classify_via_agent(prompt, images, model)
    return _parse_assignment(raw, vmat_root)


# ---------- [3] build ----------
def build(
    file_bytes: bytes,
    file_name: str,
    in_units: str,
    up_axis: str,
    assignment: dict[str, Any],
    add_light: bool = True,
) -> tuple[bytes, dict[str, Any]]:
    """원본 형상 + assignment → 재질 바인딩 자기완결 USDA 바이트 + info."""
    parts = _load_normalized(file_bytes, file_name, in_units, up_axis)
    usda, info = common.write_usd_string(
        parts, assignment["parts"], assignment["palette"], add_light=add_light
    )
    info["light"] = add_light
    return usda.encode("utf-8"), info
