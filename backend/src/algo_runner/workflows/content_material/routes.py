"""content-material 잡 제출 라우트 — 긴 WSL 파이프라인을 백그라운드 잡으로 돌린다.

POST /api/workflows/content-material/submit -> {job_id}
상태/결과: GET /api/workflows/jobs/{job_id}
(동기 /api/workflows/content-material/run 도 그대로 동작 — 짧은/직접 호출용.)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ...auth import require_auth
from .. import jobs, registry, storage
from ..material_usd import isaac
from . import handler

router = APIRouter(tags=["content-material"])

_MAX_FILE = 100 * 1024 * 1024


@router.post("/submit")
async def submit(
    file: UploadFile = File(...), _gate: None = Depends(require_auth)
) -> dict[str, Any]:
    data = await file.read()
    if len(data) > _MAX_FILE:
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다(최대 100MB).")
    name = file.filename or "asset.usd"
    ctx = registry.WorkflowContext(workflow_id="content-material")
    job_id = jobs.submit(lambda: handler.run({}, data, name, ctx))
    return {"job_id": job_id}


@router.post("/render-submit")
async def render_submit(
    asset_id: str = Form(...), views: int = Form(6), _gate: None = Depends(require_auth)
) -> dict[str, Any]:
    """자기완결 USDZ(content 결과)를 Isaac Sim 으로 멀티앵글 RTX 렌더 (잡)."""
    p = storage.asset_path(asset_id)
    if p is None:
        raise HTTPException(status_code=404, detail="에셋을 찾을 수 없습니다.")
    if not isaac.isaac_available():
        raise HTTPException(status_code=400, detail="Isaac Sim 이 이 머신에 설치돼 있지 않습니다.")
    usd_path = str(p)
    nviews = max(1, min(int(views), 12))

    def _job() -> dict[str, Any]:
        imgs = isaac.render_usd_multiangle(usd_path, views=nviews)
        recs = [
            storage.register_asset("content-material", f"isaac {n}", n, d, {"stage": "isaac-render"})
            for n, d in imgs
        ]
        return {"images": recs, "count": len(recs)}

    return {"job_id": jobs.submit(_job)}
