"""Stage E — create_simready_package.py 로 WRAPP 패키지 생성 후 zip.

SPEC §6: clean state → pre-validate → create(BOM+hash+conformance) → post-validate.
PYTHONUTF8=1 필수(cp949 크래시), 윈도우 경로 전달(msys 금지), _bom.py 패치 선적용.
WRAPP whl 없으면 --no-wrapp 폴백(BOM 없는 경량, [Package-NoBOM] 통과).
"""
from __future__ import annotations

import io
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any

from . import env


def _wrapp_available() -> bool:
    try:
        r = subprocess.run([str(env.VENV_PY), "-c", "import wrapp"],
                           capture_output=True, text=True, env=env.subprocess_env())
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def make_package(asset_dir: Path, usd_name: str, name: str, version: str,
                 license_id: str, out_root: Path, evidence: bool = False,
                 profile: str = "") -> dict[str, Any]:
    """패키지 생성 → {ok, log, no_wrapp, evidence, package_dir, zip_bytes, zip_name}.
    evidence=True 면 사전검증을 건너뛰고 evidence-form conformance 로 빌드(단일바디 FET004 면제 등 — 약한 보증)."""
    env.ensure_bom_patch()
    # clean state(SPEC §6): .metadata / 떠도는 packaging json / out_root 제거
    for stray in [asset_dir / ".metadata", asset_dir / "com.nvidia.simready.packaging.json"]:
        if stray.exists():
            shutil.rmtree(stray, ignore_errors=True) if stray.is_dir() else stray.unlink(missing_ok=True)
    if out_root.exists():
        shutil.rmtree(out_root, ignore_errors=True)
    out_root.mkdir(parents=True, exist_ok=True)

    no_wrapp = not _wrapp_available()
    args = [str(env.VENV_PY), str(env.PKGTOOL), name, version, license_id,
            str(asset_dir), str(out_root), "--root-usd", usd_name]
    if profile:   # 에셋 SimReady 프로파일로 사전검증·컨포먼스 → 그 프로파일 버전이 패키지에 기록됨
        args += ["--profile", profile]
    if no_wrapp:
        args.append("--no-wrapp")
    if evidence:   # 사전검증 우회 + evidence-form conformance(SPEC §6) — optional 면제건 빌드용
        args += ["--skip-pre-validation", "--write-evidence"]
    proc = subprocess.run(args, capture_output=True, text=True, env=env.subprocess_env(), cwd=str(env.PKG_DIR))
    log = (proc.stdout or "") + (proc.stderr or "")
    ok = proc.returncode == 0

    pkg_dir = out_root / ".packages" / name / version
    # content_hash == conformance content_hash (해시 봉인·이식성 — 보증서 §2). best-effort.
    hash_ok = None
    report_in_pkg = False
    try:
        import glob as _glob
        import json as _json
        pj = pkg_dir / "com.nvidia.simready.packaging.json"
        # conformance 파일 이름에는 프로파일명이 들어간다(...conformance.Package@1.0.0.json /
        # ...Package-Candidate@1.0.0.json). 예전엔 "*Candidate*" 만 찾아서 --profile Package 로
        # 빌드하면 이 검사가 **조용히 건너뛰어져** hash_ok=None 이 됐다(실측) → 아무 conformance 나 본다.
        cand = _glob.glob(str(pkg_dir / ".metadata" / "*conformance*.json"))
        if pj.exists() and cand:
            pjd = _json.loads(pj.read_text(encoding="utf-8"))
            ph = (pjd.get("content_hash") or {}).get("sha256")
            cfd = _json.loads(Path(cand[0]).read_text(encoding="utf-8"))
            cf = (cfd.get("content_hash") or {}).get("sha256")
            if cf:                     # Package-Candidate 계열: conformance 가 content_hash 로 봉인
                hash_ok = bool(ph) and ph == cf
            else:
                # [Package]/[Package-NoBOM] conformance 에는 content_hash 가 없다 → 대신
                # BOM 이 선언한 파일 해시가 실제 파일과 맞는지로 무결성을 확인한다.
                import hashlib as _hl
                bom = pkg_dir / ".metadata" / "com.nvidia.simready.packaging.bom.json"
                if bom.exists():
                    items = (_json.loads(bom.read_text(encoding="utf-8")) or {}).get("items") or []
                    n_ok = n_all = 0
                    for it in items:
                        rel = (it.get("relative_path") or "").replace("\\", "/")
                        want = ((it.get("hash") or {}).get("sha256") or "").lower()
                        p = pkg_dir / rel
                        if not (rel and want and p.is_file()):
                            continue
                        n_all += 1
                        if _hl.sha256(p.read_bytes()).hexdigest() == want:
                            n_ok += 1
                    hash_ok = (n_all > 0 and n_ok == n_all)
        report_in_pkg = (pkg_dir / "validation_report.json").exists()
    except Exception:  # noqa: BLE001
        hash_ok = None
    zip_bytes = b""
    zip_name = f"{name}_{version}_SimReady.zip"
    if pkg_dir.exists():
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in pkg_dir.rglob("*"):
                if f.is_file():
                    z.write(f, f.relative_to(pkg_dir))
        zip_bytes = buf.getvalue()
    return {
        "ok": ok and bool(zip_bytes),
        "log": log[-6000:],
        "no_wrapp": no_wrapp,
        "package_dir": str(pkg_dir),
        "zip_bytes": zip_bytes,
        "zip_name": zip_name,
        "hash_ok": hash_ok,                # content_hash == conformance(이식성 보장)
        "report_in_pkg": report_in_pkg,    # 검증 리포트 동봉(Q4/Q5)
        "evidence": evidence,              # evidence-form(사전검증 우회 — 약한 보증)
    }
