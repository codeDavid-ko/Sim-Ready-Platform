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
_MAX_FILE = 1024 * 1024 * 1024  # 1GB


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


def _compare_work(
    data: bytes, name: str, text: str, images: list[tuple[bytes, str]] | None = None
) -> dict[str, Any]:
    """두 엔진 실행 + 결과 병합 (동기; 잡 스레드/to_thread 에서 호출).

    images 는 **material-usd(NdotLight) 쪽 분류에만** 전달한다. content-agents 는
    자체 멀티뷰 렌더로 이미지를 만들어 쓰므로 참조 이미지를 받지 않는다."""
    s = get_settings()
    imgs = images or []
    ext = PurePath(name).suffix.lower()
    # 공정 비교: 입력을 '맨 지오메트리'로 정리해 두 엔진에 같은 자산을 준다.
    # (거대 환경 평면 제거 + 잘못된 단위 정규화 + baked 재질 바인딩 제거.) NVIDIA content-agent
    # 는 원래 raw 지오메트리 → 재질 추론 도구라, 깨진 baked 재질/평면이 있으면 멀티뷰 렌더가
    # 빈 화면이 돼 실패한다. 에이전트 로직은 그대로, 입력만 동일하게 정리.
    input_cleaned = False
    clean_meshes = 0
    if ext in (".usd", ".usda", ".usdc", ".usdz"):
        try:
            data, _ci = mu.clean_for_inference(data, name)
            input_cleaned = True
            clean_meshes = int(_ci.get("mesh_count", 0))
            # clean_for_inference 는 항상 바이너리 crate 를 반환한다 → 이후 모든 USD 읽기가
            # 원본 확장자(.usda/.usdz)로 crate 를 텍스트/zip 으로 열다 실패하지 않게 .usd 로 맞춘다.
            name = PurePath(name).stem + ".usd"
            ext = ".usd"
        except Exception:  # noqa: BLE001 -- 정리는 best-effort
            input_cleaned = False
    in_units, up_axis = _usd_units(data, ext)

    # (A) material-usd — classify 는 async → 이 스레드 전용 루프로 실행.
    # 이미지가 있으면 모드 1(전체 이미지 + 부품 이름), 없으면 모드 2(텍스트 설명).
    parts_json, _glb = mu.ingest(data, name, in_units, up_axis)
    asg_a = asyncio.run(
        mu.classify(
            parts_json, "1" if imgs else "2", text, imgs, s.vmaterials_root,
            s.anthropic_api_key, s.claude_code_oauth_token, s.claude_model,
        )
    )

    # (B) content-agents — 동기(WSL subprocess). 한쪽이 실패해도 다른 쪽 결과는 보여준다
    # (예: 입력 USD에 거대 환경 평면이 있으면 NVIDIA 멀티뷰 렌더가 빈 화면→실패). NVIDIA
    # 에이전트 자체는 비교 기준이라 손대지 않고, 실패 사유만 표면화한다.
    ctx_b = registry.WorkflowContext(workflow_id="content-material")
    content_error = None
    try:
        res_b = cm.run({}, data, name, ctx_b)
    except Exception as exc:  # noqa: BLE001
        res_b = {}
        content_error = str(exc)

    # material-usd assignment 은 부품 인덱스 키("0","1",...)다(이름 중복 대비). content-agents
    # bindings 는 부품 이름 키 → 비교 조인을 위해 ours 를 이름 키로 환산한다(중복명은 대표 1개).
    a_parts_idx = asg_a.get("parts", {})
    a_summaries = parts_json.get("parts", [])
    a_default = a_parts_idx.get("__default__")
    a_parts = {p["name"]: (a_parts_idx.get(str(i)) or a_default) for i, p in enumerate(a_summaries)}
    a_palette = asg_a.get("palette", {})
    b_bindings = res_b.get("bindings", {})
    names = sorted(set(a_parts) | set(b_bindings.keys()))
    rows = []
    for part in names:
        key_a = a_parts.get(part) or a_default
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
        usd_crate, _info = mu.build(data, name, in_units, up_axis, asg_a)
        # mu.build 는 바이너리 crate 를 반환한다 → 반드시 .usd 로 저장(.usda 로 두면 Isaac 이
        # 텍스트로 파싱하려다 스테이지 로드 실패 → "No stage found").
        render_a = storage.register_asset(
            "material-compare", "material-usd USD", f"{stem}_material_usd.usd", usd_crate, {"engine": "material-usd"}
        )
    except Exception:  # noqa: BLE001
        pass

    return {
        "ok": True,
        "input": name,
        "in_units": in_units,
        "up_axis": up_axis,
        "input_cleaned": input_cleaned,
        "clean_meshes": clean_meshes,
        "rows": rows,
        "content_asset": res_b.get("asset"),
        "content_status": res_b.get("status") or ("실패" if content_error else None),
        "content_error": content_error,
        "preview_material_usd": preview_a,
        "preview_content": res_b.get("preview"),
        "render_material_usd": render_a,            # Isaac 렌더용(자기완결 vMaterials USD)
        "render_content": res_b.get("usdz_asset"),  # Isaac 렌더용(content usdz)
    }


async def _read_validate(file: UploadFile) -> tuple[bytes, str]:
    data = await file.read()
    if len(data) > _MAX_FILE:
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다(최대 1GB).")
    name = file.filename or "asset.usd"
    if PurePath(name).suffix.lower() not in _SUPPORTED:
        raise HTTPException(status_code=400, detail="USD 형식만 비교 가능합니다(두 엔진 공통 입력).")
    return data, name


async def _read_images(images: list[UploadFile]) -> list[tuple[bytes, str]]:
    """참조 이미지 → (bytes, mime) 리스트. material-usd 분류에만 쓰인다."""
    out: list[tuple[bytes, str]] = []
    for im in images:
        b = await im.read()
        if b:
            out.append((b, im.content_type or "image/jpeg"))
    return out


@router.post("/compare-submit")
async def compare_submit(
    file: UploadFile = File(...),
    text: str = Form(""),
    images: list[UploadFile] = File(default=[]),
    _gate: None = Depends(require_auth),
) -> dict[str, Any]:
    """비교를 백그라운드 잡으로 제출 → {job_id}. (브라우저용 — 타임아웃 회피)
    images 는 NdotLight(material-usd) 분류에만 전달."""
    data, name = await _read_validate(file)
    imgs = await _read_images(images)
    job_id = jobs.submit(lambda: _compare_work(data, name, text, imgs))
    return {"job_id": job_id}


@router.post("/compare")
async def compare(
    file: UploadFile = File(...),
    text: str = Form(""),
    images: list[UploadFile] = File(default=[]),
    _gate: None = Depends(require_auth),
) -> dict[str, Any]:
    """동기 비교(직접 호출용). 두 엔진 모두 끝날 때까지 대기."""
    data, name = await _read_validate(file)
    imgs = await _read_images(images)
    try:
        return await asyncio.to_thread(_compare_work, data, name, text, imgs)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"compare 오류: {exc}") from None
