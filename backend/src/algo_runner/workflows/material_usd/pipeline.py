"""pipeline.py — material-usd 3단계(ingest / classify / build) 함수.

레퍼런스 스크립트(ingest.py/classify.py/build_usd.py)를 플랫폼에서 직접 호출 가능한
순수 함수로 옮긴 것. 파일은 바이트로 받아 임시파일에 쓰고 common 으로 처리한다.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
import time
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
    ext = os.path.splitext(file_name or "")[1].lower()
    try:
        raw = common.load_parts(path)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    # USD 는 단위(metersPerUnit)·업축을 자기서술하며 로더가 이미 월드변환·meters·Z-up 으로
    # 정규화했다. 사용자 드롭다운으로 다시 변환하면 이중 적용되므로 m→mm 만 건다.
    if ext in (".usd", ".usda", ".usdc", ".usdz"):
        return [(n, common.normalize(V, "m", "Z"), F) for n, V, F in raw]
    return [(n, common.normalize(V, in_units, up_axis), F) for n, V, F in raw]


def clean_for_inference(file_bytes: bytes, file_name: str, in_units: str = "m", up_axis: str = "Y") -> tuple[bytes, dict[str, Any]]:
    """입력 형상 → '맨 지오메트리' USD(.usd) 바이트 + info.

    로더가 월드변환·단위 정규화·환경평면 제거·baked 재질 무시를 모두 처리한 결과를 재질 없이
    자기완결 USD로 다시 쓴다. 비교(compare)에서 두 엔진에 동일한 깨끗한 raw 지오메트리를 주려는 용도.
    """
    parts = _load_normalized(file_bytes, file_name, in_units, up_axis)
    clean = common.write_geometry_usd(parts)
    return clean, {"mesh_count": len(parts)}


# ---------- [1] ingest ----------
def ingest(file_bytes: bytes, file_name: str, in_units: str, up_axis: str) -> tuple[dict[str, Any], bytes]:
    """형상 → (parts.json dict, 뷰어용 GLB 바이트)."""
    # 순수 형상 특징 추출(= LLM 인풋)만 측정. 아래 GLB(뷰어 표시용) 생성은 제외.
    _t0 = time.perf_counter()
    parts = _load_normalized(file_bytes, file_name, in_units, up_axis)
    parts_json = {
        "input": file_name,
        "in_units": in_units,
        "up_axis": up_axis,
        "part_count": len(parts),
        "parts": common.part_summary(parts),
    }
    parts_json["extract_ms"] = round((time.perf_counter() - _t0) * 1000.0, 1)
    # 뷰어용 GLB (정규화된 좌표). 재질 배정 전이라 균일 흰색이면 본체에 붙은 얇은/평평한
    # 부품(문 등)이 음영 없이 묻혀 안 보인다 → 부품마다 다른 색을 입혀 구조가 한눈에 보이게.
    # 중요: 이 색은 *뷰어 표시 전용*이다. 재질 추론(classify)·출력 USD·RTX 렌더 어디에도
    # 들어가지 않는다(classify 는 기하+참조이미지만, build 는 추론된 vMaterials 만 사용).
    # 재질로 오인되지 않게 채도 낮은 차분한 파스텔(부품 구분 오버레이)로 칠한다.
    # (이름이 모두 "Geometry"처럼 겹쳐도 node_name 을 분리해 전 부품 보존)
    import colorsys

    from trimesh.visual.material import PBRMaterial

    scene = trimesh.Scene()
    seen: dict[str, int] = {}
    for i, (n, V, F) in enumerate(parts):
        h = (0.13 + i * 0.61803398875) % 1.0  # 황금비 색상 분산(빨강서 시작 안 함)
        r, g, b = colorsys.hsv_to_rgb(h, 0.28, 0.82)  # 낮은 채도 = 차분한 파스텔
        mesh = trimesh.Trimesh(vertices=V, faces=F)
        mesh.visual = trimesh.visual.TextureVisuals(
            material=PBRMaterial(
                baseColorFactor=[int(r * 255), int(g * 255), int(b * 255), 255],
                metallicFactor=0.0,
                roughnessFactor=0.7,
            )
        )
        seen[n] = seen.get(n, 0) + 1
        node = n if seen[n] == 1 else f"{n}_{seen[n]}"
        scene.add_geometry(mesh, node_name=node)
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


def _shape_hint(size: list[float]) -> str:
    """크기 3축으로 부위 추정 힌트(이름이 의미 없을 때 LLM 이 부위를 추론하도록)."""
    s = sorted(float(x) for x in size)
    if s[2] <= 0:
        return "degenerate"
    flat = s[0] / s[2] < 0.08
    longish = s[2] / max(s[1], 1e-6) > 4
    if longish:
        return "long/thin (rail/handle/rod)"
    if flat:
        return "flat panel/plate (door/cover/sheet)"
    if s[0] / s[2] > 0.6:
        return "blocky/box (body/enclosure)"
    return "irregular"


def _group_parts(summaries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[int]]:
    """부품 요약을 기하 시그니처(정점수 + 크기 10mm 반올림)로 그룹화.

    동일/인스턴스 부품(볼트·힌지 등)을 한 그룹으로 묶어 LLM 결정 공간을 줄이고 부위별
    일관성을 높인다. 반환: (groups, part_gid). part_gid[i] = 부품 i 의 그룹 인덱스.
    """
    sig_to_gid: dict[Any, int] = {}
    groups: list[dict[str, Any]] = []
    part_gid: list[int] = []
    for i, p in enumerate(summaries):
        size = [float(x) for x in p.get("size_mm", [0, 0, 0])]
        vc = int(p.get("vertex_count", 0))
        sig = (vc, tuple(round(x, -1) for x in sorted(size)))  # 크기 10mm 반올림+정렬
        gid = sig_to_gid.get(sig)
        if gid is None:
            gid = len(groups)
            sig_to_gid[sig] = gid
            groups.append({
                "gid": gid, "count": 0, "idxs": [],
                "size_mm": [round(x, 1) for x in size],
                "centroid": p.get("centroid", [0, 0, 0]),
                "vertex_count": vc, "shape": _shape_hint(size),
            })
        groups[gid]["count"] += 1
        groups[gid]["idxs"].append(i)
        part_gid.append(gid)
    return groups, part_gid


def _default_assignment(summaries: list[dict[str, Any]], vmat_root: str) -> dict[str, Any]:
    """LLM 키가 없을 때 폴백 — 변전소 기본 팔레트의 cabinet 재질을 전 부품에 적용(인덱스 키)."""
    sub = json.loads(_SUBSTATION.read_text(encoding="utf-8"))
    spec = dict(sub.get("cabinet", {"mdl": "Metal/Steel_Painted.mdl", "subId": "Steel_Painted", "inputs": {}}))
    spec["vmat_root"] = vmat_root
    part_map = {str(i): "default" for i in range(len(summaries))}
    part_map["__default__"] = "default"
    return {
        "parts": part_map,
        "palette": {"default": spec},
        "groups": [],
        "notes": "LLM 미사용(자격증명 없음): 기본 팔레트(cabinet) 적용. 토큰/키를 넣으면 부위별 분류가 활성화됩니다.",
    }


def _build_prompt(groups: list[dict[str, Any]], mode: str, text: str) -> str:
    template = _PROMPT.read_text(encoding="utf-8")
    glist = [
        {"gid": f"g{g['gid']}", "count": g["count"], "size_mm": g["size_mm"],
         "centroid": g["centroid"], "shape": g["shape"], "vertex_count": g["vertex_count"]}
        for g in groups
    ]
    return template.format(
        mode=mode,
        groups_json=json.dumps(glist, ensure_ascii=False, indent=2),
        user_text=text or "(none)",
        catalog_json=json.dumps(_compact_catalog(), ensure_ascii=False),
    )


def _parse_group_assignment(
    raw_text: str, groups: list[dict[str, Any]], part_gid: list[int], vmat_root: str
) -> dict[str, Any]:
    """LLM 의 그룹별 배정(JSON)을 부품 인덱스별 배정으로 펼친다."""
    raw = raw_text[raw_text.find("{") : raw_text.rfind("}") + 1]
    asg = json.loads(raw)
    palette = asg.get("palette", {})
    for _k, spec in palette.items():
        spec.setdefault("vmat_root", vmat_root)
    gmap = asg.get("groups", {})  # "g0" -> material_key
    rmap = asg.get("reasons", {})  # "g0" -> 이유(한 줄)
    default_key = next(iter(palette), "default")
    parts = {str(i): (gmap.get(f"g{gid}") or default_key) for i, gid in enumerate(part_gid)}
    parts["__default__"] = default_key
    # 부품 인덱스별 '왜 이 재질인지' 이유(그룹 이유를 그 그룹 부품들에 전파)
    part_reason = {str(i): (rmap.get(f"g{gid}") or "") for i, gid in enumerate(part_gid)}
    gsummary = [
        {"gid": f"g{g['gid']}", "count": g["count"], "size_mm": g["size_mm"],
         "shape": g["shape"], "material": gmap.get(f"g{g['gid']}", default_key),
         "reason": rmap.get(f"g{g['gid']}", "")}
        for g in groups
    ]
    return {"parts": parts, "part_reason": part_reason, "palette": palette,
            "groups": gsummary, "notes": asg.get("notes", "")}


_IMG_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


async def _classify_via_agent(prompt: str, images: list[tuple[bytes, str]], model: str,
                              extra_tools: list[str] | None = None) -> str:
    """구독(claude-agent-sdk) 경로 — 이미지는 임시파일로 저장해 에이전트가 Read 한다.
    cwd 는 주지 않고 절대경로만 사용(Windows cwd 슬러그 함정 회피).

    extra_tools: 추가로 허용할 도구(예: ["WebSearch", "WebFetch"]). 질량 추론에서
    '이 제품이 실제로 몇 kg 인지'를 제조사 스펙/판매 페이지로 확인시키는 데 쓴다."""
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
            model=model, allowed_tools=["Read"] + list(extra_tools or []),
            permission_mode="bypassPermissions"
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


def _classify_via_anthropic(prompt: str, images: list[tuple[bytes, str]], api_key: str,
                            web_search: bool = False) -> str:
    """raw anthropic(API 키) 경로 — 비전 네이티브. 종량 과금 fallback.
    web_search=True 면 서버측 web_search 도구를 붙인다(질량 검산용 제품 스펙 확인)."""
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
    kw: dict[str, Any] = {}
    if web_search:
        kw["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]
    msg = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=4000,
        messages=[{"role": "user", "content": content}], **kw
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
    summaries = parts_json.get("parts", [])
    groups, part_gid = _group_parts(summaries)
    if not (api_key or oauth_token):
        return _default_assignment(summaries, vmat_root)
    prompt = _build_prompt(groups, mode, text)
    if api_key:
        raw = _classify_via_anthropic(prompt, images, api_key)
    else:
        raw = await _classify_via_agent(prompt, images, model)
    return _parse_group_assignment(raw, groups, part_gid, vmat_root)


# ---------- [3] build ----------
def to_usdz(usd_bytes: bytes) -> bytes | None:
    """build 결과 crate(.usd)를 자기완결 .usdz 로 패키징(형상 + vMaterials MDL + 텍스처 번들).

    .usd 는 형상은 품지만 MDL 을 절대경로로 참조 → 다른 PC 로 옮기면 재질이 안 잡힌다.
    usdz 는 의존 자산을 묶어 어디서나 열린다. best-effort — 실패 시 None(.usd 는 그대로 제공)."""
    import tempfile

    from pxr import Sdf, UsdUtils

    fd, src = tempfile.mkstemp(suffix=".usd")
    os.close(fd)
    fd2, dst = tempfile.mkstemp(suffix=".usdz")
    os.close(fd2)
    try:
        with open(src, "wb") as f:
            f.write(usd_bytes)
        ok = UsdUtils.CreateNewUsdzPackage(Sdf.AssetPath(src), dst)
        if ok and os.path.exists(dst) and os.path.getsize(dst) > 0:
            with open(dst, "rb") as f:
                return f.read()
        return None
    except Exception:  # noqa: BLE001 -- usdz 패키징은 best-effort
        return None
    finally:
        for p in (src, dst):
            try:
                os.remove(p)
            except OSError:
                pass


def preview_glb(
    file_bytes: bytes,
    file_name: str,
    in_units: str,
    up_axis: str,
    assignment: dict[str, Any],
) -> bytes:
    """배정 결과를 브라우저 표시용 PBR GLB 로 (색/메탈릭/러프니스 근사)."""
    from ..preview import glb_from_assignment_parts

    parts = _load_normalized(file_bytes, file_name, in_units, up_axis)
    return glb_from_assignment_parts(parts, assignment)


def build(
    file_bytes: bytes,
    file_name: str,
    in_units: str,
    up_axis: str,
    assignment: dict[str, Any],
    add_light: bool = True,
) -> tuple[bytes, dict[str, Any]]:
    """원본 형상 + assignment → 재질 바인딩 자기완결 USD(crate, .usd) 바이트 + info.

    텍스트 .usda 대신 바이너리 crate 로 내보낸다 — 수백만 정점 모델도 빠르고 작게(약 1/10),
    무손실. info["preview_text"] 에 구조 요약(텍스트 미리보기 대용)을 담는다.
    """
    parts = _load_normalized(file_bytes, file_name, in_units, up_axis)
    data, info = common.write_usd_crate(
        parts, assignment["parts"], assignment["palette"], add_light=add_light
    )
    info["light"] = add_light
    return data, info
