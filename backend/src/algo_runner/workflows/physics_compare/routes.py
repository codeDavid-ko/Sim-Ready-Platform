"""physics-compare — 같은 USD를 두 물리 엔진에 모두 돌려 물성을 비교.

(A) NdotLight mass-physics : 형상 정확 질량(부피·관성) + 고정 12종 물성표 + 구독 Claude(Stage1/2)
(B) NVIDIA content-physics : 멀티뷰 렌더(Warp) + VLM 이 밀도·질량·마찰 직접 추정(WSL)

두 엔진을 모두 돌리므로 수 분. 잡 제출(/compare-submit)을 기본으로, 동기 /compare 도 둔다.
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
from .._ca_runner import run_agent_card
from ..mass_physics import pipeline as mp
from ..material_usd import pipeline as mu_pipeline

router = APIRouter(tags=["physics-compare"])

_SUPPORTED = {".usd", ".usda", ".usdc", ".usdz"}
_MAX_FILE = 1024 * 1024 * 1024  # 1GB


def _usd_units(file_bytes: bytes, ext: str) -> str:
    from pxr import Usd, UsdGeom

    fd, p = tempfile.mkstemp(suffix=ext)
    os.close(fd)
    with open(p, "wb") as f:
        f.write(file_bytes)
    try:
        s = Usd.Stage.Open(p)
        mpu = UsdGeom.GetStageMetersPerUnit(s) or 1.0
    finally:
        try:
            os.remove(p)
        except OSError:
            pass
    return {1.0: "m", 0.01: "cm", 0.001: "mm"}.get(round(mpu, 4), "m")


def _compare_work(data: bytes, name: str, context: str, images: list[tuple[bytes, str]]) -> dict[str, Any]:
    s = get_settings()
    ext = PurePath(name).suffix.lower()
    # 공정 비교: 입력을 '맨 지오메트리'로 정리(거대 평면 제거 + 단위 정규화 + baked 재질 제거)
    # 해 두 엔진에 같은 자산을 준다. NVIDIA 렌더가 평면/깨진 재질로 빈 화면 되는 문제 회피.
    input_cleaned = False
    if ext in (".usd", ".usda", ".usdc", ".usdz"):
        try:
            data, _ci = mu_pipeline.clean_for_inference(data, name)
            input_cleaned = True
            # clean_for_inference 는 항상 바이너리 crate 반환 → 이후 USD 읽기가 원본
            # 확장자(.usda/.usdz)로 crate 를 열다 실패하지 않게 .usd 로 맞춘다.
            name = PurePath(name).stem + ".usd"
            ext = ".usd"
        except Exception:  # noqa: BLE001
            input_cleaned = False
    in_units = _usd_units(data, ext)
    stem = PurePath(name).stem

    # (A) NdotLight mass-physics
    parts = mp.parse_geometry(data, name, in_units)
    asyncio.run(
        mp.infer(parts, context=context, images=images, api_key=s.anthropic_api_key,
                 oauth_token=s.claude_code_oauth_token, model=s.claude_model)
    )
    a_table = mp.parts_table(parts)
    a_total = round(sum(p.get("mass_kg", 0) for p in parts), 4)
    a_asset = a_preview = None
    try:
        a_usda = mp.author_usd(parts)
        a_asset = storage.register_asset("physics-compare", "NdotLight 물성 USD", f"{stem}_ndot_physics.usda", a_usda, {"engine": "mass-physics"})
    except Exception:  # noqa: BLE001
        a_asset = None
    try:
        a_preview = storage.register_asset("physics-compare", "형상 미리보기", f"{stem}_preview.glb", mp.preview_glb(parts), {"stage": "preview"})
    except Exception:  # noqa: BLE001
        a_preview = None

    # (B) NVIDIA content-physics (WSL) — 실패해도 ours 결과는 보여준다(에이전트는 비교 기준).
    ctx_b = registry.WorkflowContext("content-physics")
    content_error = None
    try:
        res_b = run_agent_card("physics", data, name, ctx_b)
    except Exception as exc:  # noqa: BLE001
        res_b = {}
        content_error = str(exc)
    b_phys = res_b.get("physics", {})       # {prim: {mass?, density?}}
    b_mats = res_b.get("materials", {})      # {prim: material_name}

    a_by = {p["name"]: p for p in a_table}
    names = sorted(set(a_by) | set(b_phys) | set(b_mats))
    rows = []
    for part in names:
        a = a_by.get(part, {})
        b = b_phys.get(part, {})
        rows.append({
            "part": part,
            "ndot": {
                "material": a.get("material"), "density": a.get("density"), "mass_kg": a.get("mass_kg"),
                "static_friction": a.get("static_friction"), "restitution": a.get("restitution"),
            },
            "nvidia": {
                "material": b_mats.get(part), "density": b.get("density"), "mass_kg": b.get("mass"),
            },
        })

    return {
        "ok": True,
        "input": name,
        "in_units": in_units,
        "ndot_total_mass_kg": a_total,
        "input_cleaned": input_cleaned,
        "rows": rows,
        "ndot_asset": a_asset,            # 자기완결 UsdPhysics USD (Isaac 렌더 가능)
        "ndot_preview": a_preview,
        "nvidia_asset": res_b.get("asset"),
        "nvidia_status": res_b.get("status") or ("실패" if content_error else None),
        "nvidia_error": content_error,
        "render_ndot": a_asset,
        "render_nvidia": res_b.get("usdz_asset"),
    }


async def _read(file: UploadFile) -> tuple[bytes, str]:
    data = await file.read()
    if len(data) > _MAX_FILE:
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다(최대 1GB).")
    name = file.filename or "asset.usd"
    if PurePath(name).suffix.lower() not in _SUPPORTED:
        raise HTTPException(status_code=400, detail="USD 형식만 비교 가능합니다(두 엔진 공통 입력). STEP/STL은 '형상 → USD 변환' 카드로 먼저 변환하세요.")
    return data, name


async def _imgs(images: list[UploadFile]) -> list[tuple[bytes, str]]:
    out: list[tuple[bytes, str]] = []
    for im in images:
        b = await im.read()
        if b:
            out.append((b, im.content_type or "image/jpeg"))
    return out


@router.post("/compare-submit")
async def compare_submit(
    file: UploadFile = File(...),
    context: str = Form(""),
    images: list[UploadFile] = File(default=[]),
    _gate: None = Depends(require_auth),
) -> dict[str, Any]:
    data, name = await _read(file)
    imgs = await _imgs(images)
    return {"job_id": jobs.submit(lambda: _compare_work(data, name, context, imgs))}


@router.post("/compare")
async def compare(
    file: UploadFile = File(...),
    context: str = Form(""),
    images: list[UploadFile] = File(default=[]),
    _gate: None = Depends(require_auth),
) -> dict[str, Any]:
    data, name = await _read(file)
    imgs = await _imgs(images)
    try:
        return await asyncio.to_thread(_compare_work, data, name, context, imgs)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"compare 오류: {exc}") from None
