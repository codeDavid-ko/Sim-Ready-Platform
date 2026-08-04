"""그래스프 카드 라우트 — 형상 추출(/ingest) + AI 파지점 추론(/suggest-grasp) + grasp USD 저작(/build).

POST /api/workflows/grasp/ingest        (file) -> { meshes, up_axis, note }
POST /api/workflows/grasp/suggest-grasp (file, context) -> { points, part, reason, parts_considered }
POST /api/workflows/grasp/build         (file, points=JSON [[x,y,z],[x,y,z]]) -> { asset, info }
"""
from __future__ import annotations

import asyncio
import json
from pathlib import PurePath
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ...auth import require_auth
from ...settings import get_settings
from .. import jobs, storage
from . import pipeline

router = APIRouter(tags=["grasp"])
_WF_ID = "grasp"
_MAX_FILE = 1024 * 1024 * 1024  # 1GB


@router.post("/ingest")
async def ingest(file: UploadFile = File(...), _gate: None = Depends(require_auth)) -> dict[str, Any]:
    data = await file.read()
    if len(data) > _MAX_FILE:
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다(최대 1GB).")
    name = file.filename or "asset.usd"
    try:
        _tree, meshes, note, up_axis = pipeline.extract(data, name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"ingest 오류: {exc}") from None
    return {"ok": True, "meshes": meshes, "note": note, "up_axis": up_axis}


@router.post("/suggest-grasp")
async def suggest_grasp(
    file: UploadFile = File(...),
    context: str = Form(""),
    _gate: None = Depends(require_auth),
) -> dict[str, Any]:
    """Claude 가 형상을 보고 파지 두 점을 추론 → **잡으로 실행**(부품 많으면 LLM 이 30초 넘어 프록시 타임아웃에
    걸려 500 이 남). 프런트는 폴링. 폴링 결과 = {points, part, reason, parts_considered, llm_used}."""
    data = await file.read()
    if len(data) > _MAX_FILE:
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다(최대 1GB).")
    name = file.filename or "asset.usd"
    s = get_settings()
    if not (s.anthropic_api_key or s.claude_code_oauth_token):
        raise HTTPException(status_code=400, detail="AI 파지점 추론용 Claude 자격증명이 없습니다(.env CLAUDE_CODE_OAUTH_TOKEN).")

    # 잡 워커(별도 스레드)에서 asyncio.run — agent-SDK 서브프로세스가 winloop 메인루프와 안 엉킴.
    def _job() -> dict[str, Any]:
        points, part, reason, n_parts = asyncio.run(pipeline.ai_suggest_grasp(
            data, name, context=context,
            api_key=s.anthropic_api_key, oauth_token=s.claude_code_oauth_token, model=s.claude_model,
        ))
        return {"ok": True, "points": points, "part": part, "reason": reason,
                "parts_considered": n_parts, "llm_used": True}

    return {"job_id": jobs.submit(_job)}


@router.post("/build")
async def build(
    file: UploadFile = File(...),
    points: str = Form("[]"),
    _gate: None = Depends(require_auth),
) -> dict[str, Any]:
    data = await file.read()
    if len(data) > _MAX_FILE:
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다(최대 1GB).")
    name = file.filename or "asset.usd"
    try:
        pts = json.loads(points)
        if not (isinstance(pts, list) and len(pts) >= 2):
            raise ValueError("points 는 [[x,y,z],[x,y,z]] 형식이어야 합니다.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"points JSON 오류: {exc}") from None
    try:
        out, ext, info = pipeline.author_grasp(data, name, pts)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"build 오류: {exc}") from None
    stem = PurePath(name).stem
    asset = storage.register_asset(_WF_ID, f"{stem} (그래스프)", f"{stem}_grasp{ext}", out,
                                   {"stage": "usdz" if ext == ".usdz" else "usd", "grasp": True})
    return {"ok": True, "asset": asset, "info": info}
