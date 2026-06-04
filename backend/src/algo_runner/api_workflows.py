"""워크플로우 레지스트리 API — 셸의 3가지 역할 중 레지스트리/마운트 백엔드.

- GET  /api/workflows                                  매니페스트 목록 (카드 그리드, SCR-02)
- POST /api/workflows/{id}/run                         해당 워크플로우 실행 (마운트, SCR-03)
- GET  /api/workflows/{id}/assets/{asset_id}/download  등록된 결과물 다운로드

기존 /api/run (단일 샘플) 은 그대로 둔다. 여기서는 N개 워크플로우를 디스패치한다.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from .auth import require_auth
from .workflows import jobs, registry, storage
from .workflows.material_usd import isaac

router = APIRouter(prefix="/api/workflows", tags=["workflows"])

_MAX_FILE = 100 * 1024 * 1024  # 100MB (3D 에셋 고려)


@router.get("")
def list_workflows(_gate: None = Depends(require_auth)) -> dict[str, Any]:
    return {"workflows": registry.list_manifests()}


@router.get("/jobs/{job_id}")
def job_status(job_id: str, _gate: None = Depends(require_auth)) -> dict[str, Any]:
    """긴 워크플로우의 백그라운드 잡 상태/결과 (폴링용)."""
    return jobs.get(job_id)


@router.post("/spin-submit")
async def spin_submit(
    asset_id: str = Form(...),
    frames: int = Form(36),
    res: int = Form(540),
    _gate: None = Depends(require_auth),
) -> dict[str, Any]:
    """등록된 USD/USDZ 에셋을 Isaac RTX 로 360° 프레임 렌더(잡) → 브라우저 스핀 뷰어용.
    GET /api/workflows/jobs/{job_id} 폴링 → {frames:[download_url...], count}.
    모든 카드 공용(asset_id 만 주면 됨)."""
    p = storage.asset_path(asset_id)
    if p is None:
        raise HTTPException(status_code=404, detail="에셋을 찾을 수 없습니다.")
    if not isaac.isaac_available():
        raise HTTPException(status_code=400, detail="Isaac Sim 이 이 머신에 설치돼 있지 않습니다.")
    usd_path = str(p)
    nframes = max(8, min(int(frames), 72))
    r = max(256, min(int(res), 900))

    def _job() -> dict[str, Any]:
        imgs = isaac.render_spin_frames(usd_path, frames=nframes, res=r)
        recs = [
            storage.register_asset("spin", f"frame_{i}", f"spin_{i}.png", data, {"stage": "spin"})
            for i, data in enumerate(imgs)
        ]
        return {"frames": [rec["download_url"] for rec in recs], "count": len(recs)}

    return {"job_id": jobs.submit(_job)}


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
