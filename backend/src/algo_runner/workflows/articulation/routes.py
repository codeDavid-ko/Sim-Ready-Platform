"""articulation 라우트 — 계층 추출(/ingest) + 관절 부여 USD 저작(/build).

POST /api/workflows/articulation/ingest (file) -> { tree:[...], meshes:[{path,name,vertices,faces}] }
POST /api/workflows/articulation/build  (file, joints=JSON) -> { asset, joints }
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

router = APIRouter(tags=["articulation"])
_WF_ID = "articulation"
_MAX_FILE = 1024 * 1024 * 1024  # 1GB
_MAX_LABEL = "1GB"


@router.post("/ingest")
async def ingest(file: UploadFile = File(...), _gate: None = Depends(require_auth)) -> dict[str, Any]:
    data = await file.read()
    if len(data) > _MAX_FILE:
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다(최대 1GB).")
    name = file.filename or "asset.usd"
    try:
        tree, meshes, note, up_axis = pipeline.extract(data, name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"ingest 오류: {exc}") from None
    return {"ok": True, "tree": tree, "meshes": meshes, "note": note, "up_axis": up_axis}


@router.post("/suggest-joints")
async def suggest_joints(
    file: UploadFile = File(...),
    context: str = Form(""),
    images: list[UploadFile] = File(default=[]),   # 선택: 실물 참조 사진 → AI가 가동부·축 판단에 활용
    _gate: None = Depends(require_auth),
) -> dict[str, Any]:
    """Claude 가 부품 형상·계층(+선택 참조 이미지)을 보고 관절을 추론 → **잡으로 실행**.
    부품 많은 어셈블리는 LLM 호출이 20~40초라 동기 응답이면 프론트 프록시(30초)에 걸려 500 이 난다.
    그래서 mass_physics 처럼 job 으로 던지고, 프론트는 폴링한다. 폴링 결과 = {joints, note, parts_considered, llm_used}."""
    data = await file.read()
    if len(data) > _MAX_FILE:
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다(최대 1GB).")
    name = file.filename or "asset.usd"
    imgs: list[tuple[bytes, str]] = []
    for im in images:
        b = await im.read()
        if b:
            imgs.append((b, im.content_type or "image/jpeg"))
    s = get_settings()
    if not (s.anthropic_api_key or s.claude_code_oauth_token):
        raise HTTPException(status_code=400, detail="AI 관절 추론용 Claude 자격증명이 없습니다(.env CLAUDE_CODE_OAUTH_TOKEN).")

    # 잡 워커(별도 스레드)에서 asyncio.run — agent-SDK 서브프로세스가 winloop 메인루프와 안 엉킴.
    def _job() -> dict[str, Any]:
        joints, note, n_parts = asyncio.run(pipeline.ai_suggest_joints(
            data, name, context=context, images=imgs,
            api_key=s.anthropic_api_key, oauth_token=s.claude_code_oauth_token, model=s.claude_model,
        ))
        return {"ok": True, "joints": joints, "note": note, "parts_considered": n_parts, "llm_used": True}

    return {"job_id": jobs.submit(_job)}


@router.post("/build")
async def build(
    file: UploadFile = File(...),
    joints: str = Form("[]"),
    drive: str = Form("true"),         # 모터(drive) 포함 여부 — SimReady Prop 납품엔 불필요(옵션)
    weld_loose: str = Form("true"),    # 관절 없는 자유 강체를 제자리 고정(떨어짐 방지)
    loose_mode: str = Form("weld"),    # "weld"=베이스에 용접(강체 유지, 권장) / "static"=정적 강등(폴백)
    _gate: None = Depends(require_auth),
) -> dict[str, Any]:
    data = await file.read()
    if len(data) > _MAX_FILE:
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다(최대 1GB).")
    name = file.filename or "asset.usd"
    try:
        jlist = json.loads(joints)
        if not isinstance(jlist, list):
            raise ValueError("joints 는 배열이어야 합니다.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"joints JSON 오류: {exc}") from None
    use_drive = str(drive).lower() == "true"
    use_weld = str(weld_loose).lower() == "true"
    lmode = "static" if str(loose_mode).lower() == "static" else "weld"
    try:
        usda, authored, notes, ext = pipeline.author_preserve(data, name, jlist, drive=use_drive, weld_loose=use_weld, loose_mode=lmode)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"build 오류: {exc}") from None
    stem = PurePath(name).stem
    asset = storage.register_asset(_WF_ID, stem, f"{stem}_articulated{ext}", usda,
                                   {"stage": "usd" if ext == ".usd" else "usdz", "joints": len(authored), "drive": use_drive})
    return {"ok": True, "asset": asset, "joints": authored, "notes": notes, "drive": use_drive}
