"""trinix-model 잡 라우트 — 이미지/텍스트 → Trinix CAD → STL.

POST /api/workflows/trinix-model/submit  (form: text, images[]) -> {job_id}
GET  /api/workflows/trinix-model/ready    -> {ready}  (토큰+프롬프트 준비 여부)
GET  /api/workflows/jobs/{job_id} 로 폴링.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ...auth import require_auth
from .. import jobs, registry
from . import pipeline

router = APIRouter(tags=["trinix-model"])
_WF_ID = "trinix-model"
_MAX_FILE = 30 * 1024 * 1024


@router.get("/ready")
async def ready(_gate: dict = Depends(require_auth)) -> dict[str, Any]:
    return {"ready": pipeline.trinix_available()}


@router.post("/submit")
async def submit(
    text: str = Form(""),
    images: list[UploadFile] = File(default=[]),
    _gate: dict = Depends(require_auth),
) -> dict[str, Any]:
    if not text.strip() and not images:
        raise HTTPException(status_code=400, detail="텍스트 설명 또는 참조 이미지를 입력하세요.")
    if not pipeline.trinix_available():
        raise HTTPException(
            status_code=400,
            detail="Trinix 가 준비되지 않았습니다. .env 의 TRINIX_AI_TOKEN 과 라이브 페어링 세션(keep_session.py)을 확인하세요.",
        )
    imgs: list[tuple[bytes, str]] = []
    for im in images:
        b = await im.read()
        if len(b) > _MAX_FILE:
            raise HTTPException(status_code=413, detail="이미지가 너무 큽니다(최대 30MB).")
        if b:
            imgs.append((b, im.content_type or "image/jpeg"))
    prompt = text.strip()

    def _job() -> dict[str, Any]:
        res = pipeline.build_stl(imgs, prompt)
        ctx = registry.WorkflowContext(_WF_ID)
        stl = res["stl_bytes"]
        stl_asset = ctx.register_asset(
            display_name="trinix model", filename="model.stl", data=stl,
            meta={"engine": "trinix-cad", "source_text": prompt[:200]},
        )
        preview = None
        try:
            preview = ctx.register_asset(
                display_name="model preview", filename="model.glb",
                data=pipeline.preview_glb(stl), meta={"stage": "preview"},
            )
        except Exception:  # noqa: BLE001
            preview = None
        return {
            "engine": "Trinix CAD (MCP) · 구독 Claude 구동 · STL export",
            "stl_asset": stl_asset,
            "preview": preview,
            "report": res.get("report", "")[-1500:],
        }

    return {"job_id": jobs.submit(_job)}
