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

import asyncio
import json
from pathlib import PurePath
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ...auth import require_auth
from ...settings import get_settings
from .. import jobs, storage
from . import isaac, pipeline

router = APIRouter(tags=["material-usd"])

_WF_ID = "material-usd"
_MAX_FILE = 1024 * 1024 * 1024  # 1GB


def _register_usdz(stem: str, usd_bytes: bytes) -> dict[str, Any] | None:
    """build 결과 .usd 를 자기완결 .usdz 로 패키징해 등록(다른 PC 로 옮겨도 형상+재질 유지).
    best-effort — 실패하면 None(.usd 다운로드는 그대로)."""
    try:
        uz = pipeline.to_usdz(usd_bytes)
        if uz:
            return storage.register_asset(
                _WF_ID, f"{stem} (usdz)", f"{stem}.usdz", uz, {"stage": "usdz", "self_contained": True}
            )
    except Exception:  # noqa: BLE001
        pass
    return None


@router.post("/render-submit")
async def render_submit(
    asset_id: str = Form(...),
    frames: int = Form(48),
    _gate: None = Depends(require_auth),
) -> dict[str, Any]:
    """결과 USD(자기완결 vMaterials)를 Isaac Sim RTX 로 360° 회전 렌더 → mp4 (잡).
    GET /api/workflows/jobs/{job_id} 로 폴링 → {video: asset}."""
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


@router.post("/ingest")
async def ingest_ep(
    file: UploadFile = File(...),
    in_units: str = Form("m"),
    up_axis: str = Form("Y"),
    _gate: None = Depends(require_auth),
) -> dict[str, Any]:
    data = await file.read()
    if len(data) > _MAX_FILE:
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다(최대 1GB).")
    try:
        parts_json, glb = pipeline.ingest(data, file.filename or "model", in_units, up_axis)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"ingest 오류: {exc}") from None
    stem = PurePath(file.filename or "model").stem
    glb_asset = storage.register_asset(
        _WF_ID, f"{stem} (preview)", "preview.glb", glb, {"stage": "ingest", "source": file.filename}
    )
    return {"ok": True, "parts": parts_json, "glb": glb_asset}


@router.post("/ingest-submit")
async def ingest_submit(
    file: UploadFile = File(...),
    in_units: str = Form("m"),
    up_axis: str = Form("Y"),
    _gate: None = Depends(require_auth),
) -> dict[str, Any]:
    """ingest 의 잡 버전 — 대형 모델은 메시 로딩 + 미리보기 GLB 생성이 길어 동기 요청이
    프록시에서 끊긴다. GET /api/workflows/jobs/{job_id} 폴링 → ingest 와 동일 결과."""
    data = await file.read()
    if len(data) > _MAX_FILE:
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다(최대 1GB).")
    fname = file.filename or "model"

    def _job() -> dict[str, Any]:
        parts_json, glb = pipeline.ingest(data, fname, in_units, up_axis)
        stem = PurePath(fname).stem
        glb_asset = storage.register_asset(
            _WF_ID, f"{stem} (preview)", "preview.glb", glb, {"stage": "ingest", "source": fname}
        )
        return {"ok": True, "parts": parts_json, "glb": glb_asset}

    return {"job_id": jobs.submit(_job)}


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


@router.post("/classify-submit")
async def classify_submit(
    parts: str = Form(...),
    mode: str = Form("1"),
    text: str = Form(""),
    images: list[UploadFile] = File(default=[]),
    _gate: None = Depends(require_auth),
) -> dict[str, Any]:
    """classify 의 잡 버전 — LLM 호출이 길어(대형/다부품 모델) Next 프록시가 동기 요청을
    끊어 500 이 나는 걸 피한다. GET /api/workflows/jobs/{job_id} 폴링 → classify 와 동일 결과."""
    s = get_settings()
    try:
        parts_json = json.loads(parts)
    except ValueError:
        raise HTTPException(status_code=400, detail="parts 가 올바른 JSON 이 아닙니다.") from None
    imgs: list[tuple[bytes, str]] = [(await im.read(), im.content_type or "image/jpeg") for im in images]
    vmat_root, api_key, oauth, model = (
        s.vmaterials_root, s.anthropic_api_key, s.claude_code_oauth_token, s.claude_model
    )
    llm_used = bool(api_key or oauth)

    def _job() -> dict[str, Any]:
        asg = asyncio.run(pipeline.classify(parts_json, mode, text, imgs, vmat_root, api_key, oauth, model))
        return {"ok": True, "assignment": asg, "llm_used": llm_used}

    return {"job_id": jobs.submit(_job)}


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
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다(최대 1GB).")
    try:
        asg = json.loads(assignment)
    except ValueError:
        raise HTTPException(status_code=400, detail="assignment 가 올바른 JSON 이 아닙니다.") from None
    try:
        data_usd, info = pipeline.build(
            data, file.filename or "model", in_units, up_axis, asg, add_light=add_light.lower() == "true"
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"build 오류: {exc}") from None
    stem = PurePath(file.filename or "model").stem
    asset = storage.register_asset(
        _WF_ID, stem, f"{stem}.usd", data_usd, {"stage": "build", **info}
    )
    usdz_asset = _register_usdz(stem, data_usd)
    # 브라우저 3D 미리보기용 PBR GLB (색/메탈릭/러프니스 근사)
    preview = None
    try:
        glb = pipeline.preview_glb(data, file.filename or "model", in_units, up_axis, asg)
        preview = storage.register_asset(
            _WF_ID, f"{stem} (preview)", f"{stem}_preview.glb", glb, {"stage": "preview"}
        )
    except Exception:  # noqa: BLE001 -- preview is best-effort
        preview = None
    return {
        "ok": True,
        "asset": asset,
        "usdz_asset": usdz_asset,
        "preview": preview,
        "info": info,
        "usd_preview": info.get("preview_text", ""),
    }


@router.post("/build-submit")
async def build_submit(
    file: UploadFile = File(...),
    in_units: str = Form("m"),
    up_axis: str = Form("Y"),
    assignment: str = Form(...),
    add_light: str = Form("true"),
    _gate: None = Depends(require_auth),
) -> dict[str, Any]:
    """build 의 잡 버전 — 대형 모델(수백만 정점)은 USD 직렬화가 길어 동기 요청이 프록시에서
    끊긴다. GET /api/workflows/jobs/{job_id} 폴링 → build 와 동일 결과."""
    data = await file.read()
    if len(data) > _MAX_FILE:
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다(최대 1GB).")
    try:
        asg = json.loads(assignment)
    except ValueError:
        raise HTTPException(status_code=400, detail="assignment 가 올바른 JSON 이 아닙니다.") from None
    fname = file.filename or "model"
    light = add_light.lower() == "true"

    def _job() -> dict[str, Any]:
        data_usd, info = pipeline.build(data, fname, in_units, up_axis, asg, add_light=light)
        info["light"] = light
        stem = PurePath(fname).stem
        asset = storage.register_asset(_WF_ID, stem, f"{stem}.usd", data_usd, {"stage": "build", **info})
        usdz_asset = _register_usdz(stem, data_usd)
        preview = None
        try:
            glb = pipeline.preview_glb(data, fname, in_units, up_axis, asg)
            preview = storage.register_asset(
                _WF_ID, f"{stem} (preview)", f"{stem}_preview.glb", glb, {"stage": "preview"}
            )
        except Exception:  # noqa: BLE001 -- preview is best-effort
            preview = None
        return {
            "ok": True,
            "asset": asset,
            "usdz_asset": usdz_asset,
            "preview": preview,
            "info": info,
            "usd_preview": info.get("preview_text", ""),
        }

    return {"job_id": jobs.submit(_job)}
