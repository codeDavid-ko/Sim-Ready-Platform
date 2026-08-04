"""파이프라인 엔진 — 카드(블록)를 선형으로 엮어 출력→입력을 흘린다.

핵심: 스텝 간 **타입 매칭**. 각 스텝은 accepts(받는 타입)·produces(내는 타입)를 선언하고,
이전 스텝의 produces 가 다음 스텝의 accepts 와 호환될 때만 이어붙인다(빌더에서 차단 + 실행 전 재검증).

체이닝: 이전 스텝의 산출물 bytes 를 다음 스텝의 입력 bytes 로 그대로 전달한다. **파일명(확장자)을
정확히 붙여** 다운스트림 로더가 포맷을 올바로 디스패치하게 한다(crate→.usd, 텍스트→.usda, STEP→.step).
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import PurePath
from typing import Any, Callable

from .. import jobs, storage

# ───────────────────────── 타입 모델 ─────────────────────────
_USD_SUB = {"usd_geom", "usd_material", "usd_physics"}
_MESH_EXT = {".step", ".stp", ".stl", ".obj", ".ply", ".glb", ".gltf"}
_USD_EXT = {".usd", ".usda", ".usdc", ".usdz"}

TYPE_LABEL = {
    "mesh": "메시(STEP/STL/…)", "usd_geom": "USD(형상)", "usd_material": "USD(재질)",
    "usd_physics": "USD(물성)", "usd": "USD", "video": "영상(mp4)",
    "package": "SimReady 패키지(zip)",
}


def initial_type(filename: str) -> str | None:
    ext = PurePath(filename or "").suffix.lower()
    if ext in _MESH_EXT:
        return "mesh"
    if ext in _USD_EXT:
        return "usd_geom"
    return None


def compatible(produced: str, accepts: list[str]) -> bool:
    """produced 타입이 accepts 중 하나를 만족하나. usd_* 는 'usd' 우산에 포함."""
    if produced in accepts:
        return True
    if produced in _USD_SUB and "usd" in accepts:
        return True
    if produced == "usd" and (("usd" in accepts) or any(a in _USD_SUB for a in accepts)):
        return True
    return False


def _stem(name: str) -> str:
    return PurePath(name or "asset").stem or "asset"


# ───────────────────────── 어댑터(기존 카드 함수 재사용) ─────────────────────────
class _CaptureCtx:
    """handler.run 이 ctx.register_asset 로 등록하는 USD/USDZ 바이트를 가로채는 ctx.
    체이닝 다운스트림은 자기완결본이 필요 → usdz 가 있으면 그걸(best), 없으면 .usd 를 쓴다."""
    def __init__(self) -> None:
        self.workflow_id = "pipelines"
        self.usd: tuple[str, bytes] | None = None
        self.usdz: tuple[str, bytes] | None = None

    def register_asset(self, display_name: str = "", filename: str = "", data: bytes | None = None,
                       meta: dict | None = None, **_kw: Any) -> dict[str, Any]:
        ext = PurePath(filename).suffix.lower()
        if data:
            if ext == ".usdz" and self.usdz is None:
                self.usdz = (filename, data)
            elif ext in (".usd", ".usda", ".usdc") and self.usd is None:
                self.usd = (filename, data)
        return {"id": "_pipe_tmp", "filename": filename, "bytes": len(data or b"")}

    @property
    def best(self) -> tuple[str, bytes] | None:
        return self.usdz or self.usd


def _run_asset_prep(data: bytes, name: str, params: dict[str, Any]) -> tuple[bytes, str, str]:
    from ..asset_prep import handler as ap
    cap = _CaptureCtx()
    ap.run({}, file_bytes=data, file_name=name, ctx=cap)
    if not cap.usd:
        raise RuntimeError("asset-prep 가 USD 를 생성하지 못했습니다.")
    return cap.usd[1], f"{_stem(name)}_clean.usda", "usd_geom"


def _run_material_usd(data: bytes, name: str, params: dict[str, Any]) -> tuple[bytes, str, str]:
    from ...settings import get_settings
    from ..material_usd import pipeline as mu
    s = get_settings()
    units = params.get("scale_units") or params.get("in_units", "m")  # 단위 강제 시 mesh 입력에 반영
    up = params.get("up_axis", "Y")
    imgs = params.get("images") or []
    mode = "1" if imgs else "2"  # 이미지 있으면 모드1(전체 이미지+부품), 없으면 모드2(텍스트)
    pj, _glb = mu.ingest(data, name, units, up)
    asg = asyncio.run(mu.classify(pj, mode, params.get("text", ""), imgs,
                                  s.vmaterials_root, s.anthropic_api_key, s.claude_code_oauth_token, s.claude_model))
    usd, _info = mu.build(data, name, units, up, asg, add_light=True)  # crate → .usd
    # 카드 결과창용: 부품별 재질 + AI 선정 이유
    pal = asg.get("palette", {}); pmap = asg.get("parts", {}); rmap = asg.get("part_reason", {})
    deff = pmap.get("__default__")
    rows = []
    for i, p in enumerate(pj.get("parts", [])):
        key = pmap.get(str(i)) or deff; spec = pal.get(key, {})
        rows.append({"name": p.get("name", f"part_{i}"), "size_mm": p.get("size_mm", []),
                     "material": spec.get("subId") or key, "mdl": spec.get("mdl", ""), "reason": rmap.get(str(i), "")})
    result = {"kind": "material", "rows": rows, "notes": asg.get("notes", "")}
    return usd, f"{_stem(name)}_material.usd", "usd_material", result


def _run_mass_physics(data: bytes, name: str, params: dict[str, Any]) -> tuple[bytes, str, str]:
    from ...settings import get_settings
    from ..mass_physics import pipeline as mp
    s = get_settings()
    units = params.get("in_units", "m")
    parts = mp.parse_geometry(data, name, units, unit_override=params.get("scale_units"))
    # 앞 재질 단계가 입력 USD 에 바인딩한 시각재질을 밀도 prior 로 물려받는다(외형↔물리 밀도 일치).
    hints = mp.read_bound_materials(data, name)
    # 참조 이미지: 사용자가 올린 게 있으면 그것, 없으면 **자산을 직접 렌더**해서 보여준다.
    # 이름·치수만으로는 파란 플라스틱 통을 stainless 로 오판한다. 룩(재질/텍스처)이 없는 자산도
    # 형상은 보이므로 렌더는 항상 의미가 있다. auto_render=false 로 끌 수 있음.
    imgs = params.get("images") or None
    if not imgs and str(params.get("auto_render", "true")).lower() not in ("false", "0", "no"):
        imgs = mp.render_for_inference(data, name, views=int(params.get("render_views", 3) or 3)) or None
    # 속 빈 형상은 infer 의 Stage3 가 제품 일반무게를 보고 자동 벽두께를 정해 보정한다(UI 에서 재조정 가능).
    inf = asyncio.run(mp.infer(parts, context=params.get("context", "") or params.get("text", ""),
                               images=imgs, material_hints=hints,
                               api_key=s.anthropic_api_key, oauth_token=s.claude_code_oauth_token,
                               model=s.claude_model)) or {}
    layout = params.get("layout", "assembled")
    collision = params.get("collision", "convexHull")  # 충돌 전략 (convexHull/convexDecomposition/sdf)
    total = round(sum(p.get("mass_kg", 0) for p in parts), 3)
    solid_total = round(sum(p.get("solid_mass_kg", p.get("mass_kg", 0)) for p in parts), 3)
    result = {"kind": "physics", "parts": mp.parts_table(parts),
              "total_mass_kg": total, "solid_total_mass_kg": solid_total,
              # AI 가 본 '이 제품의 일반적 무게' — UI 에서 슬라이더 목표값으로 쓴다(없으면 None).
              "product_typical_mass_kg": inf.get("product_typical_mass_kg"),
              # 질량 양방향 검산(계산/실물 비율·판정)과 AI 가 바로잡은 재질 목록.
              "mass_check": inf.get("mass_check"),
              "material_revised": inf.get("material_revised") or [],
              "images_used": len(imgs or [])}
    ext = PurePath(name).suffix.lower()
    if ext in _USD_EXT:
        # 이전 스텝이 만든 USD(시각 vMaterials 포함) 위에 물리를 얹어 재질을 보존.
        # self_contained=True 필수: 끄면 텍스처/MDL 이 '곧 삭제되는 임시 입력파일'을 가리키는
        # usda 를 내보내 다음 스텝·렌더에서 룩이 통째로 깨진다(Isaac 에서 핑크로 나옴 —
        # 실측 픽셀차 36/255, 자기완결은 0.4/255). 산출 바이트에 맞는 확장자로 이름 붙인다.
        usd = mp.author_preserve(data, name, parts, layout, self_contained=True, collision=collision)
        out_ext = ".usdz" if usd[:2] == b"PK" else ".usd"
        return usd, f"{_stem(name)}_physics{out_ext}", "usd_physics", result
    usd = mp.author_usd(parts, layout, collision=collision)  # 메시 입력(단독) → 새로 작성
    return usd, f"{_stem(name)}_physics.usda", "usd_physics", result


def _run_turntable(data: bytes, name: str, params: dict[str, Any]) -> tuple[bytes, str, str]:
    from ..material_usd import isaac
    if not isaac.turntable_available():
        raise RuntimeError("Isaac Sim 이 설치돼 있지 않아 턴테이블 스텝을 실행할 수 없습니다.")
    ext = PurePath(name).suffix.lower()
    fd, p = tempfile.mkstemp(suffix=ext if ext in _USD_EXT else ".usd")
    os.close(fd)
    with open(p, "wb") as f:
        f.write(data)
    try:
        mp4 = isaac.render_turntable_video(
            p, turns=1, spin_dir=1, seconds=float(params.get("seconds", 8)), fps=int(params.get("fps", 24)),
            res=int(params.get("res", 1080)), zoom=False, zoom_mult=2.0,
            motors=bool(params.get("motors", False)), motor_mode="hold", motor_deg=90.0,
            clean=bool(params.get("clean", True)), motor_targets="", timeout=1800,
        )
    finally:
        try:
            os.remove(p)
        except OSError:
            pass
    return mp4, f"{_stem(name)}_turntable.mp4", "video"


def _run_content(card: str, produces: str) -> Callable[..., tuple[bytes, str, str]]:
    """NVIDIA content-agents 스텝(WSL). 자기완결 usdz 를 체인 산출물로 쓴다(.usd 는 형상 외부참조라 단독 불가)."""
    def _run(data: bytes, name: str, params: dict[str, Any]) -> tuple[bytes, str, str]:
        if card == "content-material":
            from ..content_material import handler as h
        elif card == "content-physics":
            from ..content_physics import handler as h
        else:
            from ..content_texture import handler as h
        cap = _CaptureCtx()
        h.run({}, file_bytes=data, file_name=name, ctx=cap)
        best = cap.best
        if not best:
            raise RuntimeError(f"{card}: 산출 USD 를 회수하지 못했습니다(WSL/페어링 확인).")
        return best[1], best[0], produces
    return _run


def _run_trinix(data: bytes | None, name: str, params: dict[str, Any]) -> tuple[bytes, str, str]:
    """시작 스텝: 텍스트/이미지 → Trinix CAD bbox 프록시 USD. (입력 파일 없음)"""
    from ..trinix_model import pipeline as tx
    res = tx.build_capture(params.get("images") or [], (params.get("text") or "").strip())
    pu = res.get("proxy_usd")
    if not pu:
        raise RuntimeError("Trinix proxy USD 생성 실패 — 라이브 에디터 페어링을 확인하세요.")
    return pu, "trinix_model_bbox.usd", "usd_geom"


def _run_sd_texture(data: bytes | None, name: str, params: dict[str, Any]) -> tuple[bytes, str, str]:
    """시작 스텝: 프롬프트(+선택 참조 이미지) → SD PBR 텍스처 머티리얼 USD. (입력 파일 없음)"""
    from ..sd_texture import pipeline as sd
    imgs = params.get("images") or []
    init_b, init_n = (imgs[0][0], "ref.png") if imgs else (None, None)  # images=[(bytes, ctype), ...]
    rd = sd.generate((params.get("text") or "").strip() or "material texture", size=512, steps=4, seed=0,
                     init_image=init_b, init_image_name=init_n)
    usda, usdz = sd.author_usd(rd, "sd_material")
    best, ext = (usdz, ".usdz") if usdz else (usda, ".usda")
    if not best:
        raise RuntimeError("SD 텍스처 USD 생성 실패.")
    return best, f"sd_material{ext}", "usd_material"


# ───────────────────────── 스텝 카탈로그 ─────────────────────────
# accepts: 받는 타입 목록 / produces: 내는 타입 / terminal: 뒤에 못 붙음
# inputs: 이 스텝이 (파이프라인 입력으로) 소비하는 부가 입력. text/images 는 스텝 위치와 무관하게
# 파이프라인 레벨에서 받아 해당 needs 를 가진 스텝에 전달된다(카드가 늘어도 needs 만 선언하면 패널이 맞춰짐).
_CAT_NDOT = "NdotLight (엔닷)"
_CAT_NVIDIA = "NVIDIA Content Agents"
_CAT_COMMON = "공통 (형상·렌더)"

STEPS: dict[str, dict[str, Any]] = {
    # 시작 스텝(start) — 입력 파일 없이 텍스트/이미지로 시작. 맨 앞에만 올 수 있음(accepts 비움).
    "trinix-model": {"name": "3D 모델링 (Trinix)", "icon": "🧊", "cat": _CAT_NDOT, "accepts": [], "produces": "usd_geom",
                     "start": True, "run": _run_trinix, "desc": "텍스트/이미지 → Trinix CAD bbox 프록시",
                     "inputs": {"file": [], "text": True, "images": True}},
    "sd-texture":   {"name": "텍스처 생성 (SD)", "icon": "🖌", "cat": _CAT_NDOT, "accepts": [], "produces": "usd_material",
                     "start": True, "run": _run_sd_texture, "desc": "프롬프트(+선택 이미지) → PBR 텍스처 머티리얼",
                     "inputs": {"file": [], "text": True, "images": True}},
    "asset-prep":   {"name": "형상 정리·USD 변환", "icon": "🧩", "cat": _CAT_COMMON, "accepts": ["mesh", "usd"], "produces": "usd_geom",
                     "run": _run_asset_prep, "desc": "메시/USD를 정리하고 USD로 변환",
                     "inputs": {"file": ["mesh", "usd"], "text": False, "images": False}},
    "material-usd": {"name": "재질 추론", "icon": "🎨", "cat": _CAT_NDOT, "accepts": ["mesh", "usd"], "produces": "usd_material",
                     "run": _run_material_usd, "desc": "부품별 vMaterials 재질 추론·바인딩",
                     "inputs": {"file": ["mesh", "usd"], "text": True, "images": True}},
    "mass-physics": {"name": "물성 추론", "icon": "⚖️", "cat": _CAT_NDOT, "accepts": ["mesh", "usd"], "produces": "usd_physics",
                     "run": _run_mass_physics, "desc": "질량·밀도·마찰 등 UsdPhysics 부여",
                     "inputs": {"file": ["mesh", "usd"], "text": True, "images": True},
                     "params": [{"key": "collision", "label": "충돌 전략", "type": "select", "default": "convexHull",
                                 "options": [["convexHull", "convexHull (안정·기본)"],
                                             ["convexDecomposition", "convexDecomposition (오목·안정)"],
                                             ["sdf", "sdf (정확·NVIDIA Physx용)"]]}]},
    "content-material": {"name": "재질 추론 (NVIDIA)", "icon": "🎨", "cat": _CAT_NVIDIA, "accepts": ["usd"], "produces": "usd_material",
                     "run": _run_content("content-material", "usd_material"), "desc": "NVIDIA content-agents 재질(WSL)",
                     "inputs": {"file": ["usd"], "text": False, "images": False}},
    "content-physics": {"name": "물성 추론 (NVIDIA)", "icon": "⚖️", "cat": _CAT_NVIDIA, "accepts": ["usd"], "produces": "usd_physics",
                     "run": _run_content("content-physics", "usd_physics"), "desc": "NVIDIA content-agents 물성(WSL)",
                     "inputs": {"file": ["usd"], "text": False, "images": False}},
    "content-texture": {"name": "텍스처/재질 (NVIDIA)", "icon": "🖼", "cat": _CAT_NVIDIA, "accepts": ["usd"], "produces": "usd_material",
                     "run": _run_content("content-texture", "usd_material"), "desc": "NVIDIA content-agents 텍스처(WSL)",
                     "inputs": {"file": ["usd"], "text": False, "images": False}},
    "turntable":    {"name": "턴테이블 영상", "icon": "🎬", "cat": _CAT_COMMON, "accepts": ["usd"], "produces": "video",
                     "run": _run_turntable, "terminal": True, "desc": "Isaac RTX 360° 회전 mp4",
                     "inputs": {"file": ["usd"], "text": False, "images": False}},
}


def _pipeline_cards() -> dict[str, dict[str, Any]]:
    """workflows/*/manifest.json 중 'pipeline' 블록을 선언(opt-in)한 카드를 자동으로 스텝으로 수집한다.
    각 카드는 자기 모듈에 pipeline_step.run(data, name, params) 를 제공해야 한다(없으면 실행 시 명확히 실패).
    하드코딩 STEPS 에 없는 활성/개발/비활성 모두 수집(상태 필터·실행 가드는 호출부에서). 캐시 없음 → 카드
    상태(추가/dev/disabled) 변경이 다음 호출에 바로 반영된다."""
    import json as _json
    import os as _os
    base = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))  # .../workflows
    out: dict[str, dict[str, Any]] = {}
    for entry in _os.scandir(base):
        mf = _os.path.join(entry.path, "manifest.json")
        if not entry.is_dir() or not _os.path.exists(mf):
            continue
        try:
            man = _json.loads(open(mf, encoding="utf-8").read())
        except Exception:  # noqa: BLE001
            continue
        pipe = man.get("pipeline")
        if not pipe:
            continue
        sid = str(man.get("id") or entry.name)
        if sid in STEPS:  # 하드코딩 정의 우선
            continue
        acc = list(pipe.get("accepts", ["usd"]))
        out[sid] = {
            "name": man.get("name", sid), "icon": man.get("icon", "📦"), "cat": man.get("category", "기타"),
            "accepts": acc, "produces": pipe.get("produces", "usd"),
            "terminal": bool(pipe.get("terminal", False)), "start": bool(pipe.get("start", False)),
            "run": _lazy_run(entry.name),
            "desc": man.get("tagline") or man.get("description", ""),
            "inputs": pipe.get("inputs", {"file": acc, "text": False, "images": False}),
            "params": list(pipe.get("params", [])),       # 스텝별 옵션 스키마(빌더가 자동 렌더)
            "interactive": bool(pipe.get("interactive")),  # True 면 '자동/직접' 토글(직접=실행 중 멈춰 편집)
        }
    return out


def _lazy_run(dirname: str) -> Callable[..., tuple]:
    """디스커버된 카드의 pipeline_step.run 을 지연 임포트해 호출하는 어댑터."""
    def _run(data: bytes, name: str, params: dict[str, Any]) -> tuple:
        import importlib
        mod = importlib.import_module(f"algo_runner.workflows.{dirname}.pipeline_step")
        return mod.run(data, name, params)
    return _run


def _steps() -> dict[str, dict[str, Any]]:
    """하드코딩 STEPS + 자동수집(pipeline 블록 카드) 병합 맵. STEPS 우선."""
    return {**_pipeline_cards(), **STEPS}


def get_step(sid: str) -> dict[str, Any] | None:
    return _steps().get(sid)


def _card_disabled(sid: str) -> bool:
    """해당 스텝 카드가 비활성(disabled)인지 — 저장된 파이프라인에 비활성 카드가 들어와도 안전 차단용."""
    from .. import registry
    wf = registry.get(sid)
    return bool(wf and wf.manifest.get("disabled"))


def catalog() -> list[dict[str, Any]]:
    """빌더용 — run 콜러블 제외하고 직렬화 가능한 스펙만.
    각 스텝에 해당 카드 매니페스트의 dev/disabled 를 실어, 빌더가 개발중/비활성 카드를 숨길 수 있게 한다."""
    from .. import registry
    out: list[dict[str, Any]] = []
    for k, v in _steps().items():
        wf = registry.get(k)
        man = wf.manifest if wf else {}
        out.append({
            "id": k, "name": v["name"], "icon": v["icon"], "cat": v.get("cat", "기타"),
            "accepts": v["accepts"], "produces": v["produces"], "terminal": v.get("terminal", False),
            "start": v.get("start", False),
            "dev": bool(man.get("dev")), "disabled": bool(man.get("disabled")),
            "desc": v["desc"], "inputs": v.get("inputs", {"file": v["accepts"], "text": False, "images": False}),
            "params": list(v.get("params", [])),        # 스텝별 옵션 스키마
            "interactive": bool(v.get("interactive")),   # '자동/직접' 토글 대상
        })
    return out


def validate(step_ids: list[str], init_type: str | None) -> tuple[bool, str]:
    """체인 전체 타입 호환성 검사. init_type=None 이면 업로드 파일 없음(시작 스텝 파이프라인)."""
    if not step_ids:
        return False, "스텝이 없습니다."
    sm = _steps()
    cur: str | None = None
    for i, sid in enumerate(step_ids):
        spec = sm.get(sid)
        if not spec:
            return False, f"알 수 없거나 비활성화된 스텝: {sid}"
        if _card_disabled(sid):
            return False, f"'{spec['name']}' 카드가 비활성화되어 이 파이프라인을 실행할 수 없습니다(카드 활성화 또는 스텝 제거)."
        if spec.get("start"):
            if i != 0:
                return False, f"'{spec['name']}'는 시작 스텝이라 맨 앞에만 올 수 있습니다."
            # 시작 스텝: 업스트림/파일 불필요
        elif i == 0:
            if init_type is None:
                return False, f"'{spec['name']}'는 입력 파일이 필요합니다(파일을 올리세요)."
            if not compatible(init_type, spec["accepts"]):
                want = "/".join(TYPE_LABEL.get(a, a) for a in spec["accepts"])
                return False, f"입력 파일({TYPE_LABEL.get(init_type, init_type)})이 '{spec['name']}'가 받는 {want}와 안 맞습니다."
        else:
            if sm[step_ids[i - 1]].get("terminal"):
                return False, f"'{sm[step_ids[i-1]]['name']}'는 마지막 스텝이라 뒤에 붙일 수 없습니다."
            if cur is not None and not compatible(cur, spec["accepts"]):
                want = "/".join(TYPE_LABEL.get(a, a) for a in spec["accepts"])
                return False, f"{i+1}번 '{spec['name']}'는 {want}를 받지만 이전 출력은 {TYPE_LABEL.get(cur, cur)}입니다."
        cur = spec["produces"]
    return True, "ok"


# ───────────────────────── 실행 ─────────────────────────
def run_chain(steps: list[dict[str, Any]], init_bytes: bytes, init_name: str) -> dict[str, Any]:
    """steps=[{id, params}], 초기 입력(bytes+name) → 순차 체이닝.
    각 스텝 산출물을 'pipelines' 에셋으로 등록하고 다음 입력으로 전달. 진행률은 jobs.set_progress.
    한 스텝 실패 시 그 지점에서 멈추고 부분 결과를 돌려준다(완료분 산출물은 보존)."""
    jid = jobs.current_job()
    total = len(steps)
    init_t = initial_type(init_name)
    ok, msg = validate([s["id"] for s in steps], init_t)
    if not ok:
        raise RuntimeError(f"파이프라인 타입 불일치: {msg}")

    cur_bytes, cur_name = init_bytes, init_name
    sm = _steps()
    out_steps: list[dict[str, Any]] = []
    for i, st in enumerate(steps):
        sid = st["id"]
        spec = sm.get(sid)
        if spec is None or _card_disabled(sid):   # 저장된 파이프라인의 제거/비활성 카드 안전 차단(크래시 X)
            out_steps.append({"id": sid, "name": sid, "status": "failed",
                              "error": "이 스텝의 카드가 없거나 비활성화되었습니다.", "asset": None, "type": None})
            return {"ok": False, "failed_at": i + 1, "steps": out_steps,
                    "final_asset": out_steps[-2]["asset"] if len(out_steps) >= 2 else None}
        if jid:
            # 파이프라인 스텝 진행률은 별도 슬롯(pipe)에 — 스텝 내부 카드가 progress 를 덮어써도 보존.
            jobs.set_pipeline_progress(jid, {"index": i + 1, "total": total, "label": spec["name"], "id": sid})
            jobs.set_progress(jid, None)  # 새 스텝 시작 시 이전 스텝의 내부 진행률 초기화
        try:
            out = spec["run"](cur_bytes, cur_name, st.get("params", {}) or {})
            # 어댑터는 (bytes, name, type) 또는 (bytes, name, type, result) 반환. result=카드 결과창용 구조화 데이터.
            if len(out) == 4:
                out_bytes, out_name, out_type, out_result = out
            else:
                out_bytes, out_name, out_type = out; out_result = None
        except Exception as exc:  # noqa: BLE001
            out_steps.append({"id": sid, "name": spec["name"], "status": "failed",
                              "error": str(exc)[:400], "asset": None, "type": None})
            return {"ok": False, "failed_at": i + 1, "steps": out_steps,
                    "final_asset": out_steps[-2]["asset"] if len(out_steps) >= 2 else None}
        asset = storage.register_asset(
            "pipelines", f"{spec['name']} 산출", out_name, out_bytes,
            {"stage": sid, "type": out_type, "step": i + 1},
        )
        out_steps.append({"id": sid, "name": spec["name"], "status": "done",
                          "asset": asset, "type": out_type, "type_label": TYPE_LABEL.get(out_type, out_type),
                          "result": out_result})
        cur_bytes, cur_name = out_bytes, out_name

    return {"ok": True, "steps": out_steps, "final_asset": out_steps[-1]["asset"] if out_steps else None}
