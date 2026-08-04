"""파이프라인 카드 라우트 — 스텝 카탈로그 / 타입검증 / 실행(잡) / 정의 저장·목록.

main 이 prefix=/api/workflows/pipelines 로 마운트.
  GET  /steps                스텝 카탈로그(accepts/produces) — 빌더가 타입필터에 사용
  POST /validate             {steps:[id...], init_ext} → 타입 호환성 검사
  POST /run-submit           file + steps(JSON [{id,params}]) → {job_id}; 폴링 결과 = run_chain 반환
  GET/POST/DELETE /defs      저장된 파이프라인 정의(내 파이프라인 목록)
산출물 다운로드는 제너릭 /api/workflows/pipelines/assets/{id}/download 로 처리됨.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path, PurePath
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from ...auth import require_auth
from .. import jobs
from . import engine

router = APIRouter(tags=["pipelines"])

_MAX_FILE = 1024 * 1024 * 1024  # 1GB
_DEFS = Path.home() / ".algo-runner" / "pipelines.json"


def _read_defs() -> list[dict[str, Any]]:
    try:
        return json.loads(_DEFS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _write_defs(items: list[dict[str, Any]]) -> None:
    _DEFS.parent.mkdir(parents=True, exist_ok=True)
    _DEFS.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


@router.get("/steps")
def steps(_gate: None = Depends(require_auth)) -> dict[str, Any]:
    return {"steps": engine.catalog(), "type_label": engine.TYPE_LABEL}


class ValidateIn(BaseModel):
    steps: list[str]
    init_ext: str = ""   # 초기 업로드 확장자(예: ".step", ".usd"). 비면 타입 미지정.


@router.post("/validate")
def validate(body: ValidateIn, _gate: None = Depends(require_auth)) -> dict[str, Any]:
    init_t = engine.initial_type("x" + body.init_ext) if body.init_ext else None
    ok, msg = engine.validate(body.steps, init_t)
    return {"ok": ok, "message": msg, "init_type": init_t}


@router.post("/run-submit")
async def run_submit(
    file: UploadFile | None = File(None),       # 형상(geometry/USD) — 시작 스텝(텍스트/이미지) 파이프라인이면 없음
    steps: str = Form(...),                     # JSON: [{"id": "...", "params": {"text": ...}}, ...] (스텝별 덮어쓰기 텍스트 포함)
    text: str = Form(""),                        # 공통 설명 — text 받는 모든 스텝에 적용(스텝별 자체값 있으면 그게 우선)
    images: list[UploadFile] = File(default=[]),  # 공통 참조 이미지 — images 받는 모든 스텝에 적용
    step_images: list[UploadFile] = File(default=[]),  # 스텝별 이미지(덮어쓰기) — image_map 으로 분배
    image_map: str = Form(""),                   # JSON int 배열: step_images[k] 가 몇 번째 스텝(0-base)으로 갈지.
    scale_units: str = Form(""),                 # m/mm/cm — 입력 단위 강제(비면 파일 단위/자동). 모든 스텝에 적용.
    _gate: None = Depends(require_auth),
) -> dict[str, Any]:
    data = await file.read() if file else b""
    if len(data) > _MAX_FILE:
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다(최대 1GB).")
    try:
        step_list = json.loads(steps)
        assert isinstance(step_list, list) and step_list
    except (ValueError, AssertionError):
        raise HTTPException(status_code=400, detail="steps 가 올바른 JSON 배열이 아닙니다.") from None
    fname = (file.filename if file else "") or ""
    init_t = engine.initial_type(fname) if file else None
    ok, msg = engine.validate([s.get("id") for s in step_list], init_t)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)

    def _accepts(sid: str, key: str) -> bool:
        return bool((engine.get_step(sid) or {}).get("inputs", {}).get(key))

    # 스텝별 이미지(덮어쓰기) — step_images[k] → step_list[image_map[k]]
    try:
        idx_map = json.loads(image_map) if image_map else []
        if not isinstance(idx_map, list):
            idx_map = []
    except ValueError:
        idx_map = []
    per_step_imgs: dict[int, list[tuple[bytes, str]]] = {}
    for k, im in enumerate(step_images or []):
        b = await im.read()
        if not b:
            continue
        si = int(idx_map[k]) if k < len(idx_map) else 0
        per_step_imgs.setdefault(si, []).append((b, im.content_type or "image/jpeg"))

    # 공통 이미지 — images 받는 모든 스텝에 적용(스텝별 덮어쓰기가 있으면 그게 우선)
    common_imgs: list[tuple[bytes, str]] = []
    for im in images or []:
        b = await im.read()
        if b:
            common_imgs.append((b, im.content_type or "image/jpeg"))
    for i, st in enumerate(step_list):
        p = st.setdefault("params", {})
        if i in per_step_imgs:
            p["images"] = per_step_imgs[i]
        elif common_imgs and _accepts(st.get("id"), "images"):
            p.setdefault("images", common_imgs)

    # 입력 단위 강제(m/mm/cm) — 모든 스텝에 주입(어댑터가 해당하는 것만 사용)
    su = scale_units.strip().lower()
    if su in ("m", "mm", "cm"):
        for st in step_list:
            st.setdefault("params", {})["scale_units"] = su

    # 공통 텍스트 — text 받는 스텝 중 스텝별 자체 text 가 없는 곳에 보충
    txt = text.strip()
    if txt:
        for st in step_list:
            if _accepts(st.get("id"), "text"):
                p = st.setdefault("params", {})
                p.setdefault("text", txt)
                p.setdefault("context", txt)

    def _job() -> dict[str, Any]:
        return engine.run_chain(step_list, data, fname)

    return {"job_id": jobs.submit(_job)}


@router.get("/defs")
def list_defs(_gate: None = Depends(require_auth)) -> dict[str, Any]:
    return {"pipelines": _read_defs()}


class DefIn(BaseModel):
    id: str = ""
    title: str
    steps: list[dict[str, Any]]
    formats: list[str] = []   # 이 파이프라인이 받을 입력 파일 형식(확장자). 비면 첫 스텝 기준 전체.


@router.post("/defs")
def save_def(body: DefIn, who: dict = Depends(require_auth)) -> dict[str, Any]:
    items = _read_defs()
    pid = body.id or uuid.uuid4().hex[:8]
    existing = next((x for x in items if x.get("id") == pid), None)
    author = (existing or {}).get("author") or who.get("username", "")  # 편집 시 원작자 유지
    rec = {"id": pid, "title": body.title.strip() or "파이프라인", "steps": body.steps,
           "formats": body.formats, "author": author}
    items = [x for x in items if x.get("id") != pid] + [rec]
    _write_defs(items)
    return rec


@router.delete("/defs/{pid}")
def delete_def(pid: str, _gate: None = Depends(require_auth)) -> dict[str, Any]:
    items = [x for x in _read_defs() if x.get("id") != pid]
    _write_defs(items)
    return {"ok": True}
