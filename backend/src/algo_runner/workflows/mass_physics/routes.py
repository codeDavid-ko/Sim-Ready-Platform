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
_MAX_FILE = 100 * 1024 * 1024


@router.get("/materials")
async def materials(_gate: None = Depends(require_auth)) -> dict[str, Any]:
    return {"materials": MATERIALS}


@router.post("/submit")
async def submit(
    file: UploadFile = File(...),
    in_units: str = Form("mm"),
    context: str = Form(""),
    layout: str = Form("assembled"),
    _gate: None = Depends(require_auth),
) -> dict[str, Any]:
    data = await file.read()
    if len(data) > _MAX_FILE:
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다(최대 100MB).")
    name = file.filename or "asset.stl"
    s = get_settings()
    stem = PurePath(name).stem

    def _job() -> dict[str, Any]:
        parts = pipeline.parse_geometry(data, name, in_units)
        clamp_info = asyncio.run(
            pipeline.infer(
                parts,
                context=context,
                api_key=s.anthropic_api_key,
                oauth_token=s.claude_code_oauth_token,
                model=s.claude_model,
            )
        )
        usda = pipeline.author_usd(parts, layout=layout)
        validation = pipeline.validate_usd(usda)
        ctx = registry.WorkflowContext(_WF_ID)
        asset = ctx.register_asset(
            display_name=stem, filename=f"{stem}_physics.usda", data=usda,
            meta={"stage": "usd", "layout": layout, "part_count": len(parts)},
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
        return {
            "engine": "ndotsim 물성추론 · 형상 정확 질량(trimesh) · Claude Stage1/2 (구독)",
            "in_units": in_units,
            "layout": layout,
            "llm_used": bool(s.anthropic_api_key or s.claude_code_oauth_token),
            "part_count": len(parts),
            "total_mass_kg": total_mass,
            "parts": pipeline.parts_table(parts),
            "clamped": clamp_info.get("clamped", []),
            "validation": validation,
            "asset": asset,
            "preview": preview,
        }

    return {"job_id": jobs.submit(_job)}


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
