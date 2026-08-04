"""룩 합치기 라우트 — 형상 USD + 룩 USD → 자기완결 단일 .usdz (잡).

POST /api/workflows/look-merge/merge-submit  (geometry, look, localize) -> {job_id}
폴링: GET /api/workflows/jobs/{job_id} -> {asset, report}
"""
from __future__ import annotations

from pathlib import PurePath
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ...auth import require_auth
from ...settings import get_settings
from .. import jobs, storage
from . import pipeline

router = APIRouter(tags=["look-merge"])
_WF_ID = "look-merge"
_MAX_FILE = 1024 * 1024 * 1024  # 1GB


@router.post("/merge-submit")
async def merge_submit(
    geometry: UploadFile = File(...),
    look: UploadFile = File(...),
    localize: str = Form("true"),
    remap: str = Form("false"),                        # AI 구조 매칭(경로 다를 때 부품 추론 재바인딩)
    resources: list[UploadFile] = File(default=[]),   # 룩이 참조하는 텍스처 등(있으면)
    _gate: None = Depends(require_auth),
) -> dict[str, Any]:
    geo_name = geometry.filename or "geometry.usd"
    look_name = look.filename or "look.usd"
    geo_bytes = await geometry.read()
    look_bytes = await look.read()
    if len(geo_bytes) > _MAX_FILE or len(look_bytes) > _MAX_FILE:
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다(최대 1GB).")
    if not geo_bytes or not look_bytes:
        raise HTTPException(status_code=400, detail="형상과 룩 파일을 모두 올려주세요.")
    res_list: list[tuple[str, bytes]] = []
    for rf in resources:
        rb = await rf.read()
        if rb:
            res_list.append((rf.filename or "res.bin", rb))
    do_localize = str(localize).lower() == "true"
    do_remap = str(remap).lower() == "true"
    s = get_settings()
    stem = PurePath(geo_name).stem

    def _job() -> dict[str, Any]:
        data, report = pipeline.merge(geo_bytes, geo_name, look_bytes, look_name,
                                      localize=do_localize, resources=res_list, remap=do_remap,
                                      api_key=s.anthropic_api_key, oauth_token=s.claude_code_oauth_token,
                                      model=s.claude_model)
        asset = storage.register_asset(
            _WF_ID, f"{stem} (룩 적용)", f"{stem}_shaded.usdz", data,
            {"stage": "usdz", "self_contained": report.get("self_contained")},
        )
        return {"asset": asset, "report": report}

    return {"job_id": jobs.submit(_job)}
