"""trinix-simready — 이미지/텍스트 → Trinix 3D(STEP) → 부품별 재질 + 물성 → USD.

체인:
  1) trinix_model.build_step  : 부품 보존 STEP 생성(구독 Claude + Trinix MCP)
  2) material_usd             : 부품별 vMaterials 재질 추론 → 재질 바인딩 USD
  3) mass_physics             : 부품별 질량·마찰·반발 추론 → UsdPhysics USD
STEP 은 cascadio 경유(미터·Y-up) → material_usd 는 in_units=m/up=Y, mass_physics 는 in_units=m.

긴 작업이라 잡 제출. GET /api/workflows/jobs/{id} 로 폴링.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ...auth import require_auth
from ...settings import get_settings
from .. import jobs, registry
from ..mass_physics import pipeline as mp
from ..material_usd import pipeline as mu
from ..trinix_model import pipeline as trinix

router = APIRouter(tags=["trinix-simready"])
_WF_ID = "trinix-simready"
_MAX_FILE = 1024 * 1024 * 1024  # 1GB
_NAME = "model.step"


@router.get("/ready")
async def ready(_gate: dict = Depends(require_auth)) -> dict[str, Any]:
    return {"ready": trinix.trinix_available()}


@router.post("/submit")
async def submit(
    text: str = Form(""),
    images: list[UploadFile] = File(default=[]),
    _gate: dict = Depends(require_auth),
) -> dict[str, Any]:
    if not text.strip() and not images:
        raise HTTPException(status_code=400, detail="텍스트 설명 또는 참조 이미지를 입력하세요.")
    if not trinix.trinix_available():
        raise HTTPException(status_code=400, detail="Trinix 가 준비되지 않았습니다(.env TRINIX_AI_TOKEN + 라이브 세션).")
    imgs: list[tuple[bytes, str]] = []
    for im in images:
        b = await im.read()
        if len(b) > _MAX_FILE:
            raise HTTPException(status_code=413, detail="이미지가 너무 큽니다(최대 1GB).")
        if b:
            imgs.append((b, im.content_type or "image/jpeg"))
    prompt = text.strip()

    def _job() -> dict[str, Any]:
        s = get_settings()
        ctx = registry.WorkflowContext(_WF_ID)

        # 1) Trinix → STEP(부품 보존)
        step = trinix.build_step(imgs, prompt)["step_bytes"]
        step_asset = ctx.register_asset("trinix model", "model.step", step, {"stage": "trinix", "parts_preserved": True})

        # 2) 재질 추론(부품별 vMaterials). STEP=미터·Y-up.
        parts_json, _glb = mu.ingest(step, _NAME, "m", "Y")
        asg = asyncio.run(
            mu.classify(
                parts_json, "1" if imgs else "2", prompt, imgs, s.vmaterials_root,
                s.anthropic_api_key, s.claude_code_oauth_token, s.claude_model,
            )
        )
        mat_usda, mat_info = mu.build(step, _NAME, "m", "Y", asg)
        mat_asset = ctx.register_asset("재질 USD", "model_material.usda", mat_usda, {"stage": "material", **mat_info})
        mat_preview = None
        try:
            glb = mu.preview_glb(step, _NAME, "m", "Y", asg)
            mat_preview = ctx.register_asset("재질 미리보기", "model_material.glb", glb, {"stage": "preview"})
        except Exception:  # noqa: BLE001
            mat_preview = None

        # 3) 물성 추론(부품별 질량/마찰). best-effort.
        phys_asset = None
        phys_parts: list[dict[str, Any]] = []
        try:
            parts = mp.parse_geometry(step, _NAME, "m")
            asyncio.run(mp.infer(parts, context=prompt, images=imgs, api_key=s.anthropic_api_key,
                                 oauth_token=s.claude_code_oauth_token, model=s.claude_model))
            phys_usda = mp.author_usd(parts)
            phys_asset = ctx.register_asset("물성 USD", "model_physics.usda", phys_usda, {"stage": "physics"})
            phys_parts = mp.parts_table(parts)
        except Exception as exc:  # noqa: BLE001
            phys_parts = [{"error": str(exc)}]

        # 부품별 재질+물성 병합 테이블
        a_parts = asg.get("parts", {})  # 부품 인덱스 키("0","1",...) — 이름 중복 대비
        a_palette = asg.get("palette", {})
        a_default = a_parts.get("__default__")
        phys_by_name = {p.get("name"): p for p in phys_parts if "name" in p}
        rows = []
        for i, p in enumerate(parts_json.get("parts", [])):
            part = p["name"]
            key = a_parts.get(str(i)) or a_default
            spec = a_palette.get(key, {})
            ph = phys_by_name.get(part, {})
            rows.append({
                "part": part,
                "material": spec.get("subId") or key,
                "mdl": spec.get("mdl"),
                "mass_kg": ph.get("mass_kg"),
                "density": ph.get("density"),
                "static_friction": ph.get("static_friction"),
                "restitution": ph.get("restitution"),
            })

        return {
            "engine": "Trinix CAD → 재질(vMaterials) + 물성(UsdPhysics)",
            "part_count": len(rows),
            "rows": rows,
            "step_asset": step_asset,
            "material_asset": mat_asset,
            "material_preview": mat_preview,
            "physics_asset": phys_asset,
        }

    return {"job_id": jobs.submit(_job)}
