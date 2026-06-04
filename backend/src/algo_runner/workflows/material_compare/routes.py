"""material-compare — 같은 USD를 두 재질추론 엔진에 모두 돌려 비교.

(A) material-usd  : 부품 메타데이터 + 구독 Claude → vMaterials
(B) content-agents: 멀티뷰 렌더(Warp) + 구독 Claude VLM(anthropic_oauth, WSL) → 재질 라이브러리

두 엔진을 모두 돌리므로 수 분 소요. 브라우저 프록시 타임아웃을 피하려고
잡 제출(/compare-submit -> job_id, GET /api/workflows/jobs/{id})을 기본으로 쓴다.
직접 동기 호출용 /compare 도 유지.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import PurePath
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ...auth import require_auth
from ...settings import get_settings
from .. import jobs, registry, storage
from ..content_material import handler as cm
from ..material_usd import pipeline as mu

router = APIRouter(tags=["material-compare"])

_SUPPORTED = {".usd", ".usda", ".usdc", ".usdz"}
_MAX_FILE = 100 * 1024 * 1024


def _usd_units(file_bytes: bytes, ext: str) -> tuple[str, str]:
    from pxr import Usd, UsdGeom

    fd, p = tempfile.mkstemp(suffix=ext)
    os.close(fd)
    with open(p, "wb") as f:
        f.write(file_bytes)
    try:
        s = Usd.Stage.Open(p)
        mpu = UsdGeom.GetStageMetersPerUnit(s) or 1.0
        up = str(UsdGeom.GetStageUpAxis(s))
    finally:
        try:
            os.remove(p)
        except OSError:
            pass
    units = {1.0: "m", 0.01: "cm", 0.001: "mm"}.get(round(mpu, 4), "m")
    return units, ("Z" if up.upper().startswith("Z") else "Y")


def _compare_work(data: bytes, name: str, text: str) -> dict[str, Any]:
    """두 엔진 실행 + 결과 병합 (동기; 잡 스레드/to_thread 에서 호출)."""
    s = get_settings()
    ext = PurePath(name).suffix.lower()
    in_units, up_axis = _usd_units(data, ext)

    # (A) material-usd — classify 는 async → 이 스레드 전용 루프로 실행
    parts_json, _glb = mu.ingest(data, name, in_units, up_axis)
    asg_a = asyncio.run(
        mu.classify(
            parts_json, "2", text, [], s.vmaterials_root,
            s.anthropic_api_key, s.claude_code_oauth_token, s.claude_model,
        )
    )

    # (B) content-agents — 동기(WSL subprocess)
    ctx_b = registry.WorkflowContext(workflow_id="content-material")
    res_b = cm.run({}, data, name, ctx_b)

    a_parts = asg_a.get("parts", {})
    a_palette = asg_a.get("palette", {})
    b_bindings = res_b.get("bindings", {})
    names = sorted({k for k in a_parts if k != "__default__"} | set(b_bindings.keys()))
    rows = []
    for part in names:
        key_a = a_parts.get(part) or a_parts.get("__default__")
        spec_a = a_palette.get(key_a, {})
        rows.append(
            {
                "part": part,
                "material_usd": {"key": key_a, "mdl": spec_a.get("mdl"), "subId": spec_a.get("subId")},
                "content_agents": b_bindings.get(part),
            }
        )

    from pathlib import PurePath as _PP
    stem = _PP(name).stem

    preview_a = None
    render_a = None
    try:
        glb_a = mu.preview_glb(data, name, in_units, up_axis, asg_a)
        preview_a = storage.register_asset(
            "material-compare", "material-usd preview", "material_usd_preview.glb", glb_a, {"engine": "material-usd"}
        )
        # Isaac 렌더용 자기완결 vMaterials USD
        usda, _info = mu.build(data, name, in_units, up_axis, asg_a)
        render_a = storage.register_asset(
            "material-compare", "material-usd USD", f"{stem}_material_usd.usda", usda, {"engine": "material-usd"}
        )
    except Exception:  # noqa: BLE001
        pass

    return {
        "ok": True,
        "input": name,
        "in_units": in_units,
        "up_axis": up_axis,
        "rows": rows,
        "content_asset": res_b.get("asset"),
        "content_status": res_b.get("status"),
        "preview_material_usd": preview_a,
        "preview_content": res_b.get("preview"),
        "render_material_usd": render_a,            # Isaac 렌더용(자기완결 vMaterials USD)
        "render_content": res_b.get("usdz_asset"),  # Isaac 렌더용(content usdz)
    }


async def _read_validate(file: UploadFile) -> tuple[bytes, str]:
    data = await file.read()
    if len(data) > _MAX_FILE:
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다(최대 100MB).")
    name = file.filename or "asset.usd"
    if PurePath(name).suffix.lower() not in _SUPPORTED:
        raise HTTPException(status_code=400, detail="USD 형식만 비교 가능합니다(두 엔진 공통 입력).")
    return data, name


@router.post("/compare-submit")
async def compare_submit(
    file: UploadFile = File(...), text: str = Form(""), _gate: None = Depends(require_auth)
) -> dict[str, Any]:
    """비교를 백그라운드 잡으로 제출 → {job_id}. (브라우저용 — 타임아웃 회피)"""
    data, name = await _read_validate(file)
    job_id = jobs.submit(lambda: _compare_work(data, name, text))
    return {"job_id": job_id}


@router.post("/compare")
async def compare(
    file: UploadFile = File(...), text: str = Form(""), _gate: None = Depends(require_auth)
) -> dict[str, Any]:
    """동기 비교(직접 호출용). 두 엔진 모두 끝날 때까지 대기."""
    data, name = await _read_validate(file)
    try:
        return await asyncio.to_thread(_compare_work, data, name, text)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"compare 오류: {exc}") from None
