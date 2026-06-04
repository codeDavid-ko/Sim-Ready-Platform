"""content-material 잡 제출 라우트 — 긴 WSL 파이프라인을 백그라운드 잡으로 돌린다.

POST /api/workflows/content-material/submit -> {job_id}
상태/결과: GET /api/workflows/jobs/{job_id}
(동기 /api/workflows/content-material/run 도 그대로 동작 — 짧은/직접 호출용.)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from ...auth import require_auth
from .. import jobs, registry
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
