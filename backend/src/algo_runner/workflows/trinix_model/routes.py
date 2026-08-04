"""trinix-model 잡 라우트 — 이미지/텍스트 → Trinix CAD 모델 → 다각도 스크린샷 + bbox 프록시.

Trinix 의 export_scene 이 서버측 고장이라 정밀 형상 파일은 못 받는다. 대신:
- take_screenshot/verify_views 로 **실제 RTX 스크린샷**(인라인 이미지) 회수,
- list_shapes 의 부품별 bbox 로 **bbox 프록시 USD/STL** 생성(부품수·위치·크기 정확, 블록 근사).

POST /api/workflows/trinix-model/submit (text, images[]) -> {job_id}
GET  /api/workflows/trinix-model/ready -> {ready}
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ...auth import require_auth
from .. import jobs, registry
from . import pipeline

router = APIRouter(tags=["trinix-model"])
_WF_ID = "trinix-model"
_MAX_FILE = 1024 * 1024 * 1024  # 1GB


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
        raise HTTPException(status_code=400, detail="Trinix 가 준비되지 않았습니다(.env TRINIX_AI_TOKEN + 라이브 페어링 세션).")
    imgs: list[tuple[bytes, str]] = []
    for im in images:
        b = await im.read()
        if len(b) > _MAX_FILE:
            raise HTTPException(status_code=413, detail="이미지가 너무 큽니다(최대 1GB).")
        if b:
            imgs.append((b, im.content_type or "image/jpeg"))
    prompt = text.strip()

    def _job() -> dict[str, Any]:
        res = pipeline.build_capture(imgs, prompt)
        ctx = registry.WorkflowContext(_WF_ID)
        shot_recs = []
        for i, (view, png) in enumerate(res.get("shots", [])):
            shot_recs.append(ctx.register_asset(f"shot {view}", f"shot_{i}_{view}.png", png, {"stage": "screenshot", "view": view}))
        proxy_usd = proxy_stl = None
        if res.get("proxy_usd"):
            proxy_usd = ctx.register_asset("bbox proxy USD", "model_bbox.usd", res["proxy_usd"], {"stage": "proxy-usd"})
        if res.get("proxy_stl"):
            proxy_stl = ctx.register_asset("bbox proxy STL", "model_bbox.stl", res["proxy_stl"], {"stage": "proxy-stl"})
        return {
            "engine": "Trinix CAD (MCP) · 구독 Claude · 스크린샷 + bbox 프록시",
            "shots": [r["download_url"] for r in shot_recs],
            "shape_count": len(res.get("shapes", [])),
            "shapes": res.get("shapes", []),
            "proxy_usd": proxy_usd,
            "proxy_stl": proxy_stl,
            "report": res.get("report", "")[-1200:],
        }

    return {"job_id": jobs.submit(_job)}
