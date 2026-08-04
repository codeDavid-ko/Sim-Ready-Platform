"""sd-texture 잡 라우트 — 로컬 Stable Diffusion 텍스처 생성.

POST /api/workflows/sd-texture/submit  (form: prompt, size, steps, seed) -> {job_id}
GET  /api/workflows/jobs/{job_id} 로 폴링.
GET  /api/workflows/sd-texture/ready -> {ready} (SD venv 준비 여부)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ...auth import require_auth
from .. import jobs, registry, storage
from . import pipeline

router = APIRouter(tags=["sd-texture"])
_WF_ID = "sd-texture"


@router.get("/ready")
async def ready(_gate: dict = Depends(require_auth)) -> dict[str, Any]:
    return {"ready": pipeline.sd_available()}


@router.post("/submit")
async def submit(
    prompt: str = Form(""),
    size: int = Form(768),
    steps: int = Form(4),
    seed: int = Form(0),
    strength: float = Form(0.55),
    image: UploadFile | None = File(None),
    _gate: dict = Depends(require_auth),
) -> dict[str, Any]:
    img_bytes = await image.read() if image is not None else None
    img_name = image.filename if image is not None else None
    if not prompt.strip() and not img_bytes:
        raise HTTPException(status_code=400, detail="프롬프트를 입력하거나 참조 이미지를 올리세요.")
    if not pipeline.sd_available():
        raise HTTPException(
            status_code=400,
            detail="로컬 SD 환경(~/sd_texture_venv)이 준비되지 않았습니다. 설치 후 사용하세요.",
        )
    sz = max(256, min(int(size), 1024))
    st = max(1, min(int(steps), 8))
    sd = int(seed)
    stg = max(0.1, min(float(strength), 0.99))
    p = prompt.strip()

    def _job() -> dict[str, Any]:
        rundir = pipeline.generate(
            p, size=sz, steps=st, seed=sd,
            init_image=img_bytes, init_image_name=img_name, strength=stg,
        )
        ctx = registry.WorkflowContext(_WF_ID)
        maps = {}
        for key, fname in (("albedo", "albedo.png"), ("normal", "normal.png"), ("roughness", "roughness.png")):
            p = rundir / fname
            if p.exists():
                maps[key] = ctx.register_asset(
                    display_name=f"{key}", filename=fname, data=p.read_bytes(), meta={"stage": "sd-map", "kind": key},
                )
        preview = None
        try:
            preview = ctx.register_asset(
                display_name="material swatch", filename="swatch.glb",
                data=pipeline.build_swatch_glb(rundir), meta={"stage": "preview"},
            )
        except Exception:  # noqa: BLE001
            preview = None
        usd_asset = usdz_asset = None
        try:
            usda, usdz = pipeline.author_usd(rundir)
            if usda:
                usd_asset = ctx.register_asset(
                    display_name="sd material", filename="sd_material.usda", data=usda, meta={"stage": "usd"},
                )
            if usdz:
                usdz_asset = ctx.register_asset(
                    display_name="sd material (usdz)", filename="sd_material.usdz", data=usdz, meta={"stage": "usdz", "self_contained": True},
                )
        except Exception:  # noqa: BLE001
            pass
        return {
            "engine": "Stable Diffusion (SD-Turbo) · 로컬 · 키 불필요",
            "mode": "img2img" if img_bytes else "text2img",
            "prompt": p,
            "strength": stg if img_bytes else None,
            "maps": maps,
            "preview": preview,
            "usd_asset": usd_asset,
            "usdz_asset": usdz_asset,
        }

    return {"job_id": jobs.submit(_job)}
