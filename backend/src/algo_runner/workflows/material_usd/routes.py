"""material-usd 워크플로우의 자체 엔드포인트 (다단계).

main 이 prefix=/api/workflows/material-usd 로 마운트한다. 따라서 실제 경로는:
  POST /api/workflows/material-usd/ingest     형상 업로드 → parts.json + 뷰어용 GLB(에셋)
  POST /api/workflows/material-usd/classify   parts + 이미지/설명 → assignment.json (LLM, 키 없으면 폴백)
  POST /api/workflows/material-usd/build       원본형상 + assignment → 재질 바인딩 USDA(에셋)

결과물(GLB/USDA)은 공용 다운로드 엔드포인트
  GET /api/workflows/material-usd/assets/{asset_id}/download
로 받는다(api_workflows.py).
"""

from __future__ import annotations

import json
from pathlib import PurePath
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ...auth import require_auth
from ...settings import get_settings
from .. import storage
from . import pipeline

router = APIRouter(tags=["material-usd"])

_WF_ID = "material-usd"
_MAX_FILE = 100 * 1024 * 1024  # 100MB


@router.post("/ingest")
async def ingest_ep(
    file: UploadFile = File(...),
    in_units: str = Form("m"),
    up_axis: str = Form("Y"),
    _gate: None = Depends(require_auth),
) -> dict[str, Any]:
    data = await file.read()
    if len(data) > _MAX_FILE:
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다(최대 100MB).")
    try:
        parts_json, glb = pipeline.ingest(data, file.filename or "model", in_units, up_axis)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"ingest 오류: {exc}") from None
    stem = PurePath(file.filename or "model").stem
    glb_asset = storage.register_asset(
        _WF_ID, f"{stem} (preview)", "preview.glb", glb, {"stage": "ingest", "source": file.filename}
    )
    return {"ok": True, "parts": parts_json, "glb": glb_asset}


@router.post("/classify")
async def classify_ep(
    parts: str = Form(...),
    mode: str = Form("1"),
    text: str = Form(""),
    images: list[UploadFile] = File(default=[]),
    _gate: None = Depends(require_auth),
) -> dict[str, Any]:
    s = get_settings()
    try:
        parts_json = json.loads(parts)
    except ValueError:
        raise HTTPException(status_code=400, detail="parts 가 올바른 JSON 이 아닙니다.") from None
    imgs: list[tuple[bytes, str]] = []
    for im in images:
        imgs.append((await im.read(), im.content_type or "image/jpeg"))
    try:
        asg = await pipeline.classify(
            parts_json,
            mode,
            text,
            imgs,
            s.vmaterials_root,
            s.anthropic_api_key,
            s.claude_code_oauth_token,
            s.claude_model,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"classify 오류: {exc}") from None
    return {
        "ok": True,
        "assignment": asg,
        "llm_used": bool(s.anthropic_api_key or s.claude_code_oauth_token),
    }


@router.post("/build")
async def build_ep(
    file: UploadFile = File(...),
    in_units: str = Form("m"),
    up_axis: str = Form("Y"),
    assignment: str = Form(...),
    add_light: str = Form("true"),
    _gate: None = Depends(require_auth),
) -> dict[str, Any]:
    data = await file.read()
    if len(data) > _MAX_FILE:
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다(최대 100MB).")
    try:
        asg = json.loads(assignment)
    except ValueError:
        raise HTTPException(status_code=400, detail="assignment 가 올바른 JSON 이 아닙니다.") from None
    try:
        usda, info = pipeline.build(
            data, file.filename or "model", in_units, up_axis, asg, add_light=add_light.lower() == "true"
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"build 오류: {exc}") from None
    stem = PurePath(file.filename or "model").stem
    asset = storage.register_asset(
        _WF_ID, stem, f"{stem}.usda", usda, {"stage": "build", **info}
    )
    return {
        "ok": True,
        "asset": asset,
        "info": info,
        "usd_preview": "\n".join(usda.decode("utf-8").splitlines()[:40]),
    }
