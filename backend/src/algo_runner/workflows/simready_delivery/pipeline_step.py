"""파이프라인 스텝 어댑터 — 입력 USD → SimReady 패키지(zip).
검증 → 자동수정(전체해결) → (납품 프로파일 결정) → 스탬프 → 썸네일 → 패키징.
카드 routes._job 와 동일 로직을 bytes 입력에 맞춰 재사용. terminal(파이프라인 끝)."""
from __future__ import annotations

import glob
import os
import tempfile
import zipfile
from pathlib import Path

from . import authoring, package, thumbnail, validate


def _stamp(main: str, profile: str, version: str) -> None:
    from datetime import date

    from pxr import Usd
    st = Usd.Stage.Open(main)
    md = dict(st.GetMetadata("customLayerData") or {})
    srm = dict(md.get("SimReady_Metadata", {})); srm.pop("validation", None)
    md["SimReady_Metadata"] = srm
    md["usd_date_generated"] = date.today().isoformat()
    st.SetMetadata("customLayerData", md); st.GetRootLayer().Save()
    validate.run_validate(Path(main), profile, version, stamp=True)


def run(data: bytes, name: str, params: dict):
    ext = os.path.splitext(name or "")[1].lower()
    wd = tempfile.mkdtemp(prefix="pipe_deliver_")
    if ext == ".usdz":
        zp = os.path.join(wd, "_in.usdz"); open(zp, "wb").write(data)
        with zipfile.ZipFile(zp) as z:
            z.extractall(wd)
        os.remove(zp)
    else:
        open(os.path.join(wd, "asset" + (ext or ".usda")), "wb").write(data)
    mains = [x for x in glob.glob(os.path.join(wd, "**", "*.usd*"), recursive=True)
             if not x.endswith((".png", ".usdz"))]
    if not mains:
        raise RuntimeError("납품: 입력에서 USD 를 찾지 못했습니다.")
    main = mains[0]
    usd_name = os.path.relpath(main, wd).replace("\\", "/")
    stem = Path(name or "asset").stem
    pkgname = "".join(c if (c.isalnum() or c == "_") else "_" for c in stem).strip("_").lower() or "asset"
    profile = params.get("profile") or "Prop-Robotics-Physx"
    version = params.get("version") or "1.0.0"
    license_id = params.get("license") or "CC-BY-4.0"
    cfg = {"profile": profile, "version": version, "license": license_id,
           "package_name": pkgname, "usd_name": usd_name, "source_file": name}

    # 1) 검증 + 자동수정(전체해결: optional 제외 fixable 반복)
    rep = validate.run_validate(Path(main), profile, version, report_out=Path(wd) / "validation_report.json")
    for _ in range(8):
        tt = [c["code"] for c in rep["codes"] if c.get("fixable") and not c.get("optional")]
        if not tt:
            break
        authoring.apply_fixes(Path(main), tt, cfg)
        rep = validate.run_validate(Path(main), profile, version, report_out=Path(wd) / "validation_report.json")
    if not rep["passed"] and not rep.get("deliverable"):
        raise RuntimeError("납품: 필수 검증 미해결 — " + ",".join(sorted(c["code"] for c in rep["codes"])))

    # 2) 실제 납품 프로파일 결정(요청 프로파일 깨끗이 통과 시 그걸로, 아니면 Package 폴백) + 스탬프 + 썸네일
    use_profile = profile if (rep["passed"] and profile) else "Package"
    _stamp(main, use_profile, version)
    thumbnail.render(Path(main))

    # 3) 패키징 (+ 안전망: 요청 프로파일이 사전검증에서 막히면 Package 로 재시도)
    repo = Path(tempfile.mkdtemp()) / "repo"
    res = package.make_package(Path(wd), usd_name, pkgname, version, license_id, repo,
                               evidence=False, profile=use_profile)
    if not res["ok"] and use_profile != "Package":
        use_profile = "Package"; _stamp(main, use_profile, version)
        res = package.make_package(Path(wd), usd_name, pkgname, version, license_id,
                                   Path(tempfile.mkdtemp()) / "repo", evidence=False, profile=use_profile)
    if not res.get("zip_bytes"):
        raise RuntimeError("납품: 패키징 실패 — " + (res.get("log", "")[-200:]))

    # 카드와 같은 검증 UI(DeliveryReport)를 파이프라인에서도 띄우려면 리포트 전체가 필요하다.
    # raw_stdout 은 용량만 크고 화면에 안 쓰므로 제외.
    # report.profile 은 '무엇으로 검증했나'(요청 프로파일) 그대로 둔다 — 패키징 강등은 package.downgraded 로 표시.
    report = {k: v for k, v in rep.items() if k != "raw_stdout"}
    try:
        klass = validate.classify_asset(Path(main))   # §12 입력 게이트(변형체=납품불가 등)
    except Exception:  # noqa: BLE001
        klass = None
    result = {"kind": "package", "profile_used": use_profile,
              "deliverable": bool(rep.get("deliverable")),
              "downgraded": bool(profile) and use_profile != profile,
              "remaining_codes": sorted(c["code"] for c in rep["codes"]),
              "report": report,
              "classify": klass,
              "package": {"ok": bool(res.get("ok")), "no_wrapp": bool(res.get("no_wrapp")),
                          "hash_ok": res.get("hash_ok"), "report_in_pkg": bool(res.get("report_in_pkg")),
                          "evidence": bool(res.get("evidence")),
                          "profile_used": use_profile,
                          "downgraded": bool(profile) and use_profile != profile,
                          "log": (res.get("log") or "")[-2000:]}}
    return res["zip_bytes"], f"{pkgname}_{version}_SimReady.zip", "package", result
