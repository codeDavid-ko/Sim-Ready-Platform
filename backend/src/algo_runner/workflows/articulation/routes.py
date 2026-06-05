"""articulation 라우트 — 부품 추출(/ingest) + 관절 부여 USD 저작(/build).

POST /api/workflows/articulation/ingest  (file) -> { parts:[...], glb: asset }
POST /api/workflows/articulation/build   (file, joints=JSON) -> { asset, preview, joints }
"""

from __future__ import annotations

import json
from pathlib import PurePath
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ...auth import require_auth
from .. import storage
from . import pipeline

router = APIRouter(tags=["articulation"])
_WF_ID = "articulation"
_MAX_FILE = 100 * 1024 * 1024


@router.post("/ingest")
async def ingest(file: UploadFile = File(...), _gate: None = Depends(require_auth)) -> dict[str, Any]:
    data = await file.read()
    if len(data) > _MAX_FILE:
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다(최대 100MB).")
    name = file.filename or "asset.step"
    try:
        parts = pipeline.parse_parts(data, name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"ingest 오류: {exc}") from None
    stem = PurePath(name).stem
    glb = storage.register_asset(_WF_ID, f"{stem} (viewer)", f"{stem}.glb", pipeline.viewer_glb(parts), {"stage": "viewer"})
    return {"ok": True, "parts": pipeline.parts_meta(parts), "glb": glb}


@router.post("/build")
async def build(
    file: UploadFile = File(...),
    joints: str = Form("[]"),
    _gate: None = Depends(require_auth),
) -> dict[str, Any]:
    data = await file.read()
    if len(data) > _MAX_FILE:
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다(최대 100MB).")
    name = file.filename or "asset.step"
    try:
        jlist = json.loads(joints)
        if not isinstance(jlist, list):
            raise ValueError("joints 는 배열이어야 합니다.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"joints JSON 오류: {exc}") from None
    try:
        parts = pipeline.parse_parts(data, name)
        usda, authored = pipeline.author_usd(parts, jlist)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"build 오류: {exc}") from None
    stem = PurePath(name).stem
    asset = storage.register_asset(_WF_ID, stem, f"{stem}_articulated.usda", usda,
                                   {"stage": "usd", "joints": len(authored)})
    preview = None
    try:
        preview = storage.register_asset(_WF_ID, f"{stem} (viewer)", f"{stem}.glb", pipeline.viewer_glb(parts), {"stage": "viewer"})
    except Exception:  # noqa: BLE001
        preview = None
    return {"ok": True, "asset": asset, "preview": preview, "joints": authored}
