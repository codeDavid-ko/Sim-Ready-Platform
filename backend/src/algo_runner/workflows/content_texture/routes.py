"""content-texture 잡 제출 라우트 — 긴 WSL 파이프라인을 백그라운드 잡으로.

POST /api/workflows/content-texture/submit         -> {job_id}
POST /api/workflows/content-texture/render-submit   -> {job_id}  (Isaac 360° 렌더)
상태/결과: GET /api/workflows/jobs/{job_id}
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ...auth import require_auth
from .. import jobs, registry, storage
from ..material_usd import isaac
from . import handler

router = APIRouter(tags=["content-texture"])

_MAX_FILE = 100 * 1024 * 1024
_WF_ID = "content-texture"


@router.post("/submit")
async def submit(
    file: UploadFile = File(...), _gate: None = Depends(require_auth)
) -> dict[str, Any]:
    data = await file.read()
    if len(data) > _MAX_FILE:
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다(최대 100MB).")
    name = file.filename or "asset.usd"
    ctx = registry.WorkflowContext(workflow_id=_WF_ID)
    job_id = jobs.submit(lambda: handler.run({}, data, name, ctx))
    return {"job_id": job_id}


@router.post("/render-submit")
async def render_submit(
    asset_id: str = Form(...), frames: int = Form(48), _gate: None = Depends(require_auth)
) -> dict[str, Any]:
    """자기완결 USDZ 를 Isaac Sim RTX 로 360° 회전 렌더 → mp4 (잡)."""
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
