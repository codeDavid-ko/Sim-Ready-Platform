"""NVIDIA SimReady Delivery 카드 — USD 올리고 프로파일 지정 → 검증 → 실패 fix(개별/전체) → 패키지.

세션은 파일 기반(env.SESSIONS/<sid>/): asset/<usd> + materials + .thumbs + config.json + pkg_repo.
엔드포인트:
  GET  /profiles                프로파일/버전 + 환경 가용성
  POST /submit  (file+config)   세션 생성·작업본 작성·1차 검증           -> job -> {session_id, config, report}
  POST /fix     (session,codes) 선택 실패코드 수정 후 재검증              -> job -> {applied, report}
  POST /package (session)       썸네일+WRAPP 패키지 후 zip 등록           -> job -> {ok, log, asset, no_wrapp}
"""
from __future__ import annotations

import io
import json
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ...auth import require_auth
from .. import jobs, storage, usdz_util
from . import authoring, env, package, preflight, thumbnail, validate

router = APIRouter(tags=["simready-delivery"])
_WF_ID = "nvidia-simready-delivery"
_MAX_FILE = 1024 * 1024 * 1024


def _sess_dir(sid: str) -> Path:
    d = env.SESSIONS / sid
    if not d.exists() or not (d / "config.json").exists():
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다(만료되었거나 잘못된 ID).")
    return d


def _load_cfg(sid: str) -> dict[str, Any]:
    return json.loads((_sess_dir(sid) / "config.json").read_text(encoding="utf-8"))


@router.get("/profiles")
def profiles(_gate: None = Depends(require_auth)) -> dict[str, Any]:
    ok, msg = env.available()
    # 판정은 100% NVIDIA 코드(simready-validate 엔진 + capabilities/**/validation.py, Apache-2.0).
    # 이 카드는 에셋 저작·수정·호출만 한다(SPEC v3 §0).
    engine = {
        "name": "NVIDIA simready-validate",
        "rules": "capabilities/**/validation.py (NVIDIA SimReady Foundation)",
        "license": "Apache-2.0 · © NVIDIA CORPORATION",
        "packager": "create_simready_package.py (WRAPP)",
        "note": "검증·패키징 판정은 NVIDIA 원본 코드가 결정. 이 카드는 에셋 저작/수정과 호출만 합니다.",
    }
    return {"profiles": env.PROFILES, "available": ok, "message": msg,
            "default_profile": "Prop-Robotics-Physx", "engine": engine}


@router.get("/preflight")
def preflight_check(_gate: None = Depends(require_auth)) -> dict[str, Any]:
    """§11a 자가검사 — SPEC 가정이 실제 NVIDIA 소스와 일치하는지 + _bom 패치 + Pillow."""
    ok, msg = env.available()
    if not ok:
        return {"ok": False, "available": False, "message": msg, "checks": []}
    res = preflight.run_preflight()
    res["available"] = True
    return res


@router.post("/submit")
async def submit(
    file: UploadFile = File(...),
    profile: str = Form("Prop-Robotics-Physx"),
    version: str = Form("1.0.0"),
    package_name: str = Form("asset_a01"),
    license_id: str = Form("CC-BY-4.0"),
    company: str = Form("ndotlight"),
    contact_name: str = Form(""),
    contact_email: str = Form(""),
    asset_kind: str = Form("prop"),
    source_meters_per_unit: str = Form("auto"),   # "auto"=파일의 metersPerUnit 사용 / 또는 숫자(수동 강제)
    grasp_points: str = Form(""),
    _gate: None = Depends(require_auth),
) -> dict[str, Any]:
    ok, msg = env.available()
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    data = await file.read()
    if len(data) > _MAX_FILE:
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다(최대 1GB).")
    src_name = file.filename or "asset.usd"
    ext = Path(src_name).suffix.lower()
    if ext not in (".usd", ".usda", ".usdc", ".usdz"):
        raise HTTPException(status_code=400, detail="USD 형식만 지원합니다(.usd/.usda/.usdc/.usdz).")
    # 패키지명 → 고유 ID 보장. 기본값("asset_a01")이거나 비면 업로드 파일명에서 유도(에셋마다 달라짐).
    # package_id = com.nvidia.simready.{name}.{version} 이라 name 이 상수면 모든 패키지 ID 가 같아진다.
    pkg_src = (package_name or "").strip()
    if pkg_src.lower() in ("", "asset_a01"):
        pkg_src = Path(src_name).stem or "asset_a01"
    pkg = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in pkg_src).strip("_").lower() or "asset"
    try:
        gpts = json.loads(grasp_points) if grasp_points.strip() else None
    except ValueError:
        gpts = None

    def _job() -> dict[str, Any]:
        import os as _os
        import tempfile
        from pxr import Usd, UsdGeom
        sid = uuid.uuid4().hex[:12]
        sdir = env.SESSIONS / sid
        adir = sdir / "asset"
        adir.mkdir(parents=True, exist_ok=True)
        detected_mpu = 1.0
        if ext == ".usdz":
            # usdz(zip 패키지)는 추출 — 내부 머티리얼/텍스처가 함께 와야 참조가 안 깨짐(자기완결 유지).
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = z.namelist()
                z.extractall(adir)
            cand = [n for n in names if n.lower().endswith((".usd", ".usda", ".usdc"))]
            top = [n for n in cand if "/" not in n.strip("/")]
            usd_name = (top or cand or [f"sm_{pkg}.usd"])[0]
            try:
                st = Usd.Stage.Open(str(adir / usd_name))
                if st:
                    detected_mpu = float(UsdGeom.GetStageMetersPerUnit(st) or 1.0)
            except Exception:  # noqa: BLE001
                pass
        else:
            # 단일 레이어 USD는 flatten → 자기완결 작업본 .usd
            fd, tmp = tempfile.mkstemp(suffix=ext)
            _os.close(fd); Path(tmp).write_bytes(data)
            usd_name = f"sm_{pkg}.usd"
            try:
                stage = Usd.Stage.Open(tmp)
                if stage is None:
                    raise RuntimeError("USD 를 열지 못했습니다.")
                detected_mpu = float(UsdGeom.GetStageMetersPerUnit(stage) or 1.0)
                stage.Flatten().Export(str(adir / usd_name))
            finally:
                try:
                    _os.remove(tmp)
                except OSError:
                    pass
        cfg = {
            "profile": profile, "version": version, "package_name": pkg, "license": license_id,
            "company": company, "contact": {"name": contact_name, "email": contact_email},
            "asset_kind": asset_kind, "source_meters_per_unit": source_meters_per_unit,
            "detected_mpu": detected_mpu,  # 파일이 선언한 metersPerUnit(자동 변환 배율)
            "grasp_points": gpts, "source_file": src_name, "usd_name": usd_name,
        }
        klass = validate.classify_asset(adir / usd_name)   # §12 입력 게이트(변형체=납품불가 등)
        cfg["gate_level"] = klass.get("gate_level", "ok")
        sdir.joinpath("config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        report = validate.run_validate(adir / usd_name, profile, version)
        return {"session_id": sid, "config": cfg, "report": report, "classify": klass}

    return {"job_id": jobs.submit(_job)}


@router.post("/download-usd")
async def download_usd(
    session_id: str = Form(...),
    _gate: None = Depends(require_auth),
) -> dict[str, Any]:
    """검증·수정된 USD를 의존자산(머티리얼/텍스처/썸네일)까지 묶어 .zip 으로 등록(자기완결).
    이메일 제출은 하지 않음 — 사용자가 이 USD 와 패키지를 받아 직접 제출."""
    sdir = _sess_dir(session_id)
    cfg = _load_cfg(session_id)
    adir = sdir / "asset"

    def _job() -> dict[str, Any]:
        try:
            thumbnail.render(adir / cfg["usd_name"])   # 없으면 만들어 함께 번들
        except Exception:  # noqa: BLE001
            pass
        # 우선 자기완결 .usdz 로(단일 파일·재업로드 가능·어디서나 열림). 실패 시 폴더 zip 폴백.
        uz = usdz_util.package_usdz(adir / cfg["usd_name"])
        if uz:
            rec = storage.register_asset(
                _WF_ID, f"{cfg['package_name']} {cfg['version']} (자기완결 USD)", f"{cfg['package_name']}.usdz",
                uz, {"stage": "usd-usdz", "self_contained": True})
            return {"asset": rec}
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in adir.rglob("*"):
                if f.is_file():
                    z.write(f, f.relative_to(adir))
        rec = storage.register_asset(
            _WF_ID, f"{cfg['package_name']} {cfg['version']} (USD+자산)", f"{cfg['package_name']}_usd.zip",
            buf.getvalue(), {"stage": "usd-bundle"})
        return {"asset": rec}

    return {"job_id": jobs.submit(_job)}


@router.post("/fix")
async def fix(
    session_id: str = Form(...),
    codes: str = Form("all"),   # JSON 리스트 또는 "all"
    _gate: None = Depends(require_auth),
) -> dict[str, Any]:
    sdir = _sess_dir(session_id)
    cfg = _load_cfg(session_id)
    usd_path = sdir / "asset" / cfg["usd_name"]

    def _job() -> dict[str, Any]:
        # SPEC v5 §13: 검증은 단일패스 신뢰 금지 — 'all'이면 고정점까지 반복(HI.004 고치면 물리/그래스프
        # 가 새로 드러남=정상). 특정 코드 지정이면 1회만.
        is_all = codes.strip() == "all"
        applied: list[dict[str, Any]] = []
        rep = validate.run_validate(usd_path, cfg["profile"], cfg["version"])
        # 이 에셋에서 실제 자동수정 가능한 코드(per-asset 판정 — 예: 메시 1개면 RB.MB.001 비자동)
        def _auto(r):
            return [c["code"] for c in r["codes"] if c["fixable"]]
        if not is_all:
            try:
                want = set(json.loads(codes))
            except ValueError:
                want = set()
            target = [c for c in _auto(rep) if c in want]
            if target:
                applied = authoring.apply_fixes(usd_path, target, cfg)
                rep = validate.run_validate(usd_path, cfg["profile"], cfg["version"])
            return {"session_id": session_id, "applied": applied, "report": rep}
        # 고정점 루프(최대 6회): 이번에 고칠 게 없으면 중단(자동 불가 항목은 제외돼 무한루프 안 됨).
        for _ in range(6):
            target = _auto(rep)
            if not target:
                break
            applied += authoring.apply_fixes(usd_path, target, cfg)
            rep = validate.run_validate(usd_path, cfg["profile"], cfg["version"])
        return {"session_id": session_id, "applied": applied, "report": rep}

    return {"job_id": jobs.submit(_job)}


@router.post("/enrich")
async def enrich(
    session_id: str = Form(...),
    _gate: None = Depends(require_auth),
) -> dict[str, Any]:
    """빠진 물리(충돌체·강체·질량·물리머티리얼)+그래스프를 보강 → 내용 없는 에셋을 실제 PhysX 프롭으로."""
    sdir = _sess_dir(session_id)
    cfg = _load_cfg(session_id)
    usd_path = sdir / "asset" / cfg["usd_name"]

    def _job() -> dict[str, Any]:
        applied = authoring.enrich_physics(usd_path, cfg)
        rep = validate.run_validate(usd_path, cfg["profile"], cfg["version"])
        return {"session_id": session_id, "applied": applied, "report": rep}

    return {"job_id": jobs.submit(_job)}


@router.post("/package")
async def make_package(
    session_id: str = Form(...),
    _gate: None = Depends(require_auth),
) -> dict[str, Any]:
    sdir = _sess_dir(session_id)
    cfg = _load_cfg(session_id)
    adir = sdir / "asset"
    usd_path = adir / cfg["usd_name"]

    def _job() -> dict[str, Any]:
        if cfg.get("gate_level") == "block":
            return {"ok": False, "message": "이 에셋 종류는 SimReady 납품 프로파일이 없어 패키지할 수 없습니다(변형체 등). NVIDIA DevRel에 문의하세요."}
        # 1) 검증(스탬프는 아직 안 함) — 프로파일 결정 + 리포트 저장(패키지 동봉, PDF Q4/Q5).
        rep = validate.run_validate(usd_path, cfg["profile"], cfg["version"],
                                    report_out=adir / "validation_report.json")
        if not rep["passed"] and not rep.get("deliverable"):
            return {"ok": False, "report": rep,
                    "message": "아직 통과하지 못한 (필수) 검증 항목이 있어 패키지할 수 없습니다. 먼저 해결하세요."}
        # 프로파일 결정: 요청 프로파일을 '깨끗이' 통과하면 그걸로, 아니면 자산이 실제 통과하는 'Package' 로 폴백.
        # (과거 버그: deliverable이지만 미통과 시 evidence 모드로 빌드 → conformance/root_usds 누락.)
        requested = cfg.get("profile", "")
        use_profile = requested if (rep["passed"] and requested) else "Package"

        def _stamp(profile: str) -> None:
            # 재스탬프 전 오래된 검증 스탬프(날짜별 누적)·날짜 제거 후, '실제 납품 프로파일'로 스탬프.
            # (과거 버그: 요청 프로파일로 스탬프돼 conformance와 불일치 + 옛 날짜가 누적돼 남음.)
            from datetime import date
            from pxr import Usd
            st = Usd.Stage.Open(str(usd_path))
            md = dict(st.GetMetadata("customLayerData") or {})
            srm = dict(md.get("SimReady_Metadata", {})); srm.pop("validation", None)
            md["SimReady_Metadata"] = srm
            md["usd_date_generated"] = date.today().isoformat()
            st.SetMetadata("customLayerData", md); st.GetRootLayer().Save()
            validate.run_validate(usd_path, profile, cfg["version"], stamp=True)

        _stamp(use_profile)
        thumbnail.render(usd_path)
        res = package.make_package(adir, cfg["usd_name"], cfg["package_name"], cfg["version"],
                                   cfg["license"], sdir / "pkg_repo", evidence=False, profile=use_profile)
        # 안전망: 요청 프로파일로 시도했는데 사전검증에서 막히면 Package 로 재스탬프 후 재시도.
        if not res["ok"] and use_profile != "Package":
            use_profile = "Package"; _stamp(use_profile)
            res = package.make_package(adir, cfg["usd_name"], cfg["package_name"], cfg["version"],
                                       cfg["license"], sdir / "pkg_repo", evidence=False, profile=use_profile)
        downgraded = bool(requested) and use_profile != requested
        asset = None
        if res["zip_bytes"]:
            tag = f" · {use_profile}" if downgraded else ""
            asset = storage.register_asset(
                _WF_ID, f"{cfg['package_name']} {cfg['version']} (SimReady{tag})", res["zip_name"],
                res["zip_bytes"], {"stage": "package", "no_wrapp": res["no_wrapp"],
                                   "profile_used": use_profile, "downgraded": downgraded})
        msg = None
        if downgraded:
            msg = (f"요청 프로파일 '{requested}'의 필수 기능(예: 다중바디 articulation/FET004)을 충족하지 못해, "
                   f"자산이 실제로 통과하는 'Package' 프로파일로 납품했습니다. 단순 리지드 프롭(움직이는 부품 없음)은 정상입니다.")
        return {"ok": res["ok"], "log": res["log"], "no_wrapp": res["no_wrapp"],
                "profile_used": use_profile, "downgraded": downgraded, "message": msg,
                "asset": asset, "report": rep, "package_dir": res["package_dir"],
                "hash_ok": res.get("hash_ok"), "report_in_pkg": res.get("report_in_pkg")}

    return {"job_id": jobs.submit(_job)}
