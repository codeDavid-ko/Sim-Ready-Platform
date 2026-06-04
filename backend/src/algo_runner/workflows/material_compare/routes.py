"""material-compare 워크플로우 — 같은 USD를 두 재질추론 엔진에 모두 돌려 비교.

(A) material-usd  : 부품 메타데이터 + 구독 Claude(claude-agent-sdk) → vMaterials
(B) content-agents: 멀티뷰 렌더(Warp) + 구독 Claude VLM(anthropic_oauth, WSL) → 재질 라이브러리

부품 이름으로 두 결과를 매칭해 나란히 반환한다. 두 파이프라인을 모두 돌리므로
수 분 소요(특히 B). routes 기반(다단계) 워크플로우.
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
from .. import registry
from ..content_material import handler as cm
from ..material_usd import pipeline as mu

router = APIRouter(tags=["material-compare"])

_SUPPORTED = {".usd", ".usda", ".usdc", ".usdz"}
_MAX_FILE = 100 * 1024 * 1024


def _usd_units(file_bytes: bytes, ext: str) -> tuple[str, str]:
    """USD 의 metersPerUnit/upAxis 를 material-usd 의 in_units/up_axis 로 매핑."""
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


@router.post("/compare")
async def run(
    file: UploadFile = File(...),
    text: str = Form(""),
    _gate: None = Depends(require_auth),
) -> dict[str, Any]:
    s = get_settings()
    data = await file.read()
    if len(data) > _MAX_FILE:
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다(최대 100MB).")
    name = file.filename or "asset.usd"
    ext = PurePath(name).suffix.lower()
    if ext not in _SUPPORTED:
        raise HTTPException(status_code=400, detail="USD 형식만 비교 가능합니다(두 엔진 공통 입력).")

    in_units, up_axis = _usd_units(data, ext)

    # (A) material-usd — 부품 메타 + 구독 Claude → vMaterials
    try:
        parts_json, _glb = mu.ingest(data, name, in_units, up_axis)
        asg_a = await mu.classify(
            parts_json, "2", text, [], s.vmaterials_root,
            s.anthropic_api_key, s.claude_code_oauth_token, s.claude_model,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"material-usd 오류: {exc}") from None

    # (B) content-agents — 멀티뷰 렌더 + VLM (WSL, 동기 subprocess → 스레드)
    ctx_b = registry.WorkflowContext(workflow_id="content-material")
    try:
        res_b = await asyncio.to_thread(cm.run, {}, data, name, ctx_b)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"content-agents 오류: {exc}") from None

    a_parts = asg_a.get("parts", {})
    a_palette = asg_a.get("palette", {})
    b_bindings = res_b.get("bindings", {})

    names = sorted(
        {k for k in a_parts if k != "__default__"} | set(b_bindings.keys())
    )
    rows = []
    for part in names:
        key_a = a_parts.get(part) or a_parts.get("__default__")
        spec_a = a_palette.get(key_a, {})
        rows.append(
            {
                "part": part,
                "material_usd": {
                    "key": key_a,
                    "mdl": spec_a.get("mdl"),
                    "subId": spec_a.get("subId"),
                },
                "content_agents": b_bindings.get(part),
            }
        )

    return {
        "ok": True,
        "input": name,
        "in_units": in_units,
        "up_axis": up_axis,
        "rows": rows,
        "content_asset": res_b.get("asset"),
        "content_status": res_b.get("status"),
    }
