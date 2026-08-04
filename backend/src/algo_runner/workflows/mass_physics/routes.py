"""mass-physics 잡 라우트 — STEP/STL → 질량특성 + Claude 물성추론 → UsdPhysics .usd.

POST /api/workflows/mass-physics/submit         -> {job_id}
POST /api/workflows/mass-physics/render-submit   -> {job_id}  (Isaac 360° 렌더)
GET  /api/workflows/jobs/{job_id}                -> 상태/결과
GET  /api/workflows/mass-physics/materials       -> reference 가드레일 테이블
"""

from __future__ import annotations

import asyncio
from pathlib import PurePath
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ...auth import require_auth
from ...settings import get_settings
from .. import jobs, registry, storage
from ..material_usd import isaac
from . import pipeline
from .reference import MATERIALS

router = APIRouter(tags=["mass-physics"])

_WF_ID = "mass-physics"
_MAX_FILE = 1024 * 1024 * 1024  # 1GB


@router.get("/materials")
async def materials(_gate: None = Depends(require_auth)) -> dict[str, Any]:
    return {"materials": MATERIALS}


@router.get("/part-materials/{asset_id}")
async def part_materials(asset_id: str, _gate: None = Depends(require_auth)) -> dict[str, Any]:
    """물성 USD 의 부품별 현재 재질 — 사람이 고칠 UI 가 초기값으로 쓴다."""
    p = storage.asset_path(asset_id)
    if p is None:
        raise HTTPException(status_code=404, detail="에셋을 찾을 수 없습니다.")
    try:
        return {"parts": pipeline.current_materials(p.read_bytes(), p.name), "materials": MATERIALS}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"부품 재질 조회 실패: {e}") from e


@router.post("/material-reauthor")
async def material_reauthor(
    asset_id: str = Form(...),
    overrides: str = Form("{}"),        # JSON {부품이름 또는 prim경로: 재질키}
    collision: str = Form("convexHull"),
    _gate: None = Depends(require_auth),
) -> dict[str, Any]:
    """AI 추론이 틀린 부품의 재질을 사람이 지정한 값으로 바꿔 즉시 재작성(LLM 미사용).
    질량=밀도×부피로 다시 계산하고, 룩(시각재질·UV·텍스처)은 그대로 보존한다."""
    import json as _json

    rec = next((r for r in storage.list_assets() if r["id"] == asset_id), None)
    p = storage.asset_path(asset_id)
    if rec is None or p is None:
        raise HTTPException(status_code=404, detail="에셋을 찾을 수 없습니다.")
    try:
        ov = _json.loads(overrides or "{}")
        if not isinstance(ov, dict):
            raise ValueError("overrides 는 {부품: 재질} 형태여야 합니다.")
        bad = [v for v in ov.values() if v not in MATERIALS]
        if bad:
            raise ValueError("알 수 없는 재질: " + ", ".join(sorted(set(bad))))
        out, applied = pipeline.reauthor_materials(p.read_bytes(), p.name, ov, collision=collision)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"재질 재작성 실패: {e}") from e
    ext = ".usdz" if out[:4] == b"PK\x03\x04" else ".usda"
    stem = PurePath(rec["filename"]).stem.replace("_physics", "")
    total = round(sum(float(a.get("mass_kg") or 0) for a in applied), 3)
    new = storage.register_asset(
        rec.get("workflow_id", _WF_ID), f"{rec['name']} (재질 수정 {len(ov)}건)",
        f"{stem}_physics_mat{ext}", out,
        {"stage": "usd", "material_overrides": ov, "from": asset_id, "total_mass_kg": total},
    )
    return {"asset": new, "applied": applied, "total_mass_kg": total}


@router.post("/submit")
async def submit(
    file: UploadFile = File(...),
    in_units: str = Form("mm"),
    context: str = Form(""),
    layout: str = Form("assembled"),
    preserve: str = Form("false"),     # 구조 보존(경로 유지) — 입력 USD 위에 물리만 얹음(룩·계층 유지)
    collision: str = Form("convexHull"),  # 충돌 전략: convexHull/convexDecomposition/sdf/boundingCube
    images: list[UploadFile] = File(default=[]),
    _gate: None = Depends(require_auth),
) -> dict[str, Any]:
    data = await file.read()
    if len(data) > _MAX_FILE:
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다(최대 1GB).")
    name = file.filename or "asset.stl"
    s = get_settings()
    stem = PurePath(name).stem
    do_preserve = str(preserve).lower() == "true"
    _USD_IN = PurePath(name).suffix.lower() in (".usd", ".usda", ".usdc", ".usdz")
    imgs: list[tuple[bytes, str]] = []
    for im in images:
        b = await im.read()
        if b:
            imgs.append((b, im.content_type or "image/jpeg"))

    def _job() -> dict[str, Any]:
        parts = pipeline.parse_geometry(data, name, in_units)
        hints = pipeline.read_bound_materials(data, name)  # USD에 재질 바인딩 있으면 밀도 prior 로 사용
        clamp_info = asyncio.run(
            pipeline.infer(
                parts,
                context=context,
                images=imgs,
                material_hints=hints,
                api_key=s.anthropic_api_key,
                oauth_token=s.claude_code_oauth_token,
                model=s.claude_model,
            )
        )
        if do_preserve and _USD_IN:
            # 구조 보존: 입력 USD 구조·prim 경로·시각 재질(룩)을 그대로 두고 물리만 제자리에 얹음 → 자기완결 마감.
            usda = pipeline.author_preserve(data, name, parts, layout=layout, self_contained=True, collision=collision)
            ext = ".usdz" if usda[:2] == b"PK" else (".usd" if usda[:8] == b"PXR-USDC" else ".usda")
            validation = {"ok": True, "note": "구조 보존 모드(검증 생략 — 입력 구조 유지)"}
        else:
            usda = pipeline.author_usd(parts, layout=layout, collision=collision)
            ext = ".usda"
            validation = pipeline.validate_usd(usda)
        ctx = registry.WorkflowContext(_WF_ID)
        asset = ctx.register_asset(
            display_name=stem, filename=f"{stem}_physics{ext}", data=usda,
            meta={"stage": "usd" if ext != ".usdz" else "usdz", "layout": layout,
                  "part_count": len(parts), "preserve": do_preserve},
        )
        preview = None
        try:
            preview = ctx.register_asset(
                display_name=f"{stem} (preview)", filename=f"{stem}_preview.glb",
                data=pipeline.preview_glb(parts), meta={"stage": "preview"},
            )
        except Exception:  # noqa: BLE001
            preview = None
        total_mass = round(sum(p.get("mass_kg", 0) for p in parts), 4)
        solid_total = round(sum(p.get("solid_mass_kg", p.get("mass_kg", 0)) for p in parts), 4)
        return {
            "engine": "ndotsim 물성추론 · 형상 정확 질량(trimesh) · Claude Stage1/2/3 (구독)",
            "in_units": in_units,
            "layout": layout,
            "llm_used": bool(s.anthropic_api_key or s.claude_code_oauth_token),
            "part_count": len(parts),
            "total_mass_kg": total_mass,
            "solid_total_mass_kg": solid_total,   # 보정 전(솔리드) 총질량 — 비교용
            "auto_shell_applied": clamp_info.get("auto_shell_applied", []),
            "parts": pipeline.parts_table(parts),
            "clamped": clamp_info.get("clamped", []),
            "validation": validation,
            "asset": asset,
            "preview": preview,
        }

    return {"job_id": jobs.submit(_job)}


@router.post("/shell-reauthor")
async def shell_reauthor(
    asset_id: str = Form(...),
    shell_mm: float = Form(-1.0),   # <0 = 부품별 AI 자동, 0 = 솔리드, >0 = 모든 적격 부품에 그 두께(mm)
    _gate: None = Depends(require_auth),
) -> dict[str, Any]:
    """기존 물성 USD 를 두께 shell_mm 로 즉시 재작성(LLM·메시 재계산 없음) → 새 에셋 등록."""
    rec = next((r for r in storage.list_assets() if r["id"] == asset_id), None)
    p = storage.asset_path(asset_id)
    if rec is None or p is None:
        raise HTTPException(status_code=404, detail="에셋을 찾을 수 없습니다.")
    try:
        out = pipeline.reauthor_shell(p.read_bytes(), p.name, shell_mm)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"쉘 재작성 실패: {e}") from e
    label = "AI자동" if shell_mm < 0 else ("솔리드" if shell_mm == 0 else f"{shell_mm:g}mm")
    stem = PurePath(rec["filename"]).stem.replace("_physics", "")
    new = storage.register_asset(
        rec.get("workflow_id", _WF_ID), f"{rec['name']} (쉘 {label})",
        f"{stem}_physics_shell.usda", out, {"stage": "usd", "shell_mm": shell_mm, "from": asset_id},
    )
    return {"asset": new}


@router.post("/render-submit")
async def render_submit(
    asset_id: str = Form(...), frames: int = Form(48), _gate: None = Depends(require_auth)
) -> dict[str, Any]:
    p = storage.asset_path(asset_id)
    if p is None:
        raise HTTPException(status_code=404, detail="에셋을 찾을 수 없습니다.")
    if not isaac.isaac_available():
        raise HTTPException(status_code=400, detail="Isaac Sim 이 이 머신에 설치돼 있지 않습니다.")
    usd_path = str(p)
    nframes = max(8, min(int(frames), 120))

    def _job() -> dict[str, Any]:
        mp4 = isaac.render_turntable(usd_path, frames=nframes)
        rec = storage.register_asset(_WF_ID, "turntable", "turntable.mp4", mp4, {"stage": "isaac-turntable"})
        return {"video": rec}

    return {"job_id": jobs.submit(_job)}
