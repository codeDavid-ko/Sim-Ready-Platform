"""워크플로우 레지스트리 API — 셸의 3가지 역할 중 레지스트리/마운트 백엔드.

- GET  /api/workflows                                  매니페스트 목록 (카드 그리드, SCR-02)
- POST /api/workflows/{id}/run                         해당 워크플로우 실행 (마운트, SCR-03)
- GET  /api/workflows/{id}/assets/{asset_id}/download  등록된 결과물 다운로드

기존 /api/run (단일 샘플) 은 그대로 둔다. 여기서는 N개 워크플로우를 디스패치한다.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from .auth import require_auth
from .workflows import registry, storage

router = APIRouter(prefix="/api/workflows", tags=["workflows"])

_MAX_FILE = 100 * 1024 * 1024  # 100MB (3D 에셋 고려)


@router.get("")
def list_workflows(_gate: None = Depends(require_auth)) -> dict[str, Any]:
    return {"workflows": registry.list_manifests()}


@router.post("/{workflow_id}/run")
async def run_workflow(
    workflow_id: str,
    request: Request,
    file: UploadFile | None = File(default=None),
    _gate: None = Depends(require_auth),
) -> dict[str, Any]:
    wf = registry.get(workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail=f"워크플로우를 찾을 수 없습니다: {workflow_id}")
    if wf.handler is None:
        raise HTTPException(
            status_code=400,
            detail=f"이 워크플로우는 원샷 실행을 제공하지 않습니다(자체 엔드포인트 사용): {workflow_id}",
        )

    form = await request.form()
    params: dict[str, Any] = {
        k: v for k, v in form.items() if k != "file" and isinstance(v, str)
    }

    file_bytes: bytes | None = None
    file_name: str | None = None
    if file is not None:
        file_bytes = await file.read()
        if len(file_bytes) > _MAX_FILE:
            raise HTTPException(status_code=413, detail="파일이 너무 큽니다(최대 100MB).")
        file_name = file.filename

    ctx = registry.WorkflowContext(workflow_id=workflow_id)
    try:
        result = wf.handler(params, file_bytes, file_name, ctx)
    except Exception as exc:  # noqa: BLE001  (워크플로우 오류를 사용자에게 전달)
        raise HTTPException(status_code=400, detail=f"워크플로우 오류: {exc}") from None
    return {"ok": True, "result": result}


@router.get("/{workflow_id}/assets/{asset_id}/download")
def download_asset(
    workflow_id: str, asset_id: str, _gate: None = Depends(require_auth)
) -> FileResponse:
    p = storage.asset_path(asset_id)
    if p is None:
        raise HTTPException(status_code=404, detail="에셋을 찾을 수 없습니다.")
    return FileResponse(path=str(p), filename=p.name, media_type="application/octet-stream")
