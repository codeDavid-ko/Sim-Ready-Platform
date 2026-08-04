"""POST /api/run — 폼 파라미터 + (선택) 파일 → run_algorithm() → 결과 JSON.

프런트는 FormData 로 보낸다: 임의 텍스트 필드들(params) + 선택 file 1개.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from .algorithm import run_algorithm
from .auth import require_auth

router = APIRouter(prefix="/api", tags=["run"])

_MAX_FILE = 1024 * 1024 * 1024  # 1GB


@router.post("/run")
async def run(
    request: Request,
    file: UploadFile | None = File(default=None),
    _gate: None = Depends(require_auth),
) -> dict[str, Any]:
    # file 외의 모든 폼 필드를 params 로 수집
    form = await request.form()
    params: dict[str, Any] = {k: v for k, v in form.items() if k != "file" and isinstance(v, str)}

    file_bytes: bytes | None = None
    file_name: str | None = None
    if file is not None:
        file_bytes = await file.read()
        if len(file_bytes) > _MAX_FILE:
            raise HTTPException(status_code=413, detail="파일이 너무 큽니다(최대 1GB).")
        file_name = file.filename

    try:
        result = run_algorithm(params, file_bytes, file_name)
    except Exception as exc:  # noqa: BLE001  (알고리즘 오류를 사용자에게 전달)
        raise HTTPException(status_code=400, detail=f"알고리즘 오류: {exc}") from None
    return {"ok": True, "result": result}
