"""NVIDIA SimReady Foundation 납품 환경 — 이 머신의 클론 레포/venv/검증기 경로 + 가용성·패치.

검증·패키징은 simready-foundation 의 전용 venv(py3.11 + simready-validate + pxr + wrapp)로
서브프로세스 호출한다(algo_runner venv 아님). 저작(pxr 변형)·썸네일은 in-process pxr 로 한다.
경로/함정은 SimReady_Delivery_PROGRAM_SPEC.md + 메모리 [[simready-oem-delivery]] 기준(검증됨).
"""
from __future__ import annotations

import os
from pathlib import Path

REPO = Path(r"C:\Users\user\Downloads\simready-foundation")
VENV_PY = REPO / ".venv" / "Scripts" / "python.exe"
VALIDATE_EXE = REPO / ".venv" / "Scripts" / "simready-validate.exe"
DOCS = REPO / "nv_core" / "sr_specs" / "docs"
CAPS = DOCS / "capabilities"
FEATURES = DOCS / "features"
PROFILES_TOML = DOCS / "profiles" / "profiles.toml"
PKGTOOL = REPO / "nv_core" / "package_sample" / "create_simready_package.py"
PKG_DIR = REPO / "nv_core" / "package_sample"
_BOM = REPO / "nv_core" / "package_sample" / "sr_pkg_sample" / "_bom.py"

SESSIONS = Path.home() / ".algo-runner" / "simready_sessions"

# 프로파일 결정트리(SPEC §3) — 비로봇 프롭 3계열/5엔트리.
PROFILES: dict[str, list[str]] = {
    "Prop-Robotics-Physx": ["1.0.0", "2.0.0"],
    "Prop-Robotics-Neutral": ["1.0.0", "2.0.0"],
    "Prop-Robotics-Isaac": ["1.0.0"],
}


def available() -> tuple[bool, str]:
    """이 머신에서 SimReady 검증/패키징이 가능한지(레포·venv·검증기·docs 존재)."""
    miss = [str(p) for p in (VENV_PY, VALIDATE_EXE, PROFILES_TOML, PKGTOOL) if not p.exists()]
    if miss:
        return False, "SimReady Foundation 환경 없음: " + ", ".join(miss)
    return True, "ok"


def subprocess_env() -> dict[str, str]:
    """검증/패키징 서브프로세스용 env — cp949 크래시 회피(PYTHONUTF8=1)."""
    e = dict(os.environ)
    e["PYTHONUTF8"] = "1"
    return e


def ensure_bom_patch() -> bool:
    """크로스플랫폼 버그 패치(SPEC §1.2) 멱등 적용 — 윈도우 백슬래시 경로로 content_hash mismatch 나는 것 수정.
    재클론하면 사라지므로 패키징 전에 매번 보장. 이미 적용돼 있으면 True 반환(변경 없음)."""
    if not _BOM.exists():
        return False
    txt = _BOM.read_text(encoding="utf-8")
    orig = txt
    # compute_bom 의 rel = entry.relative_path → 슬래시 정규화
    needle = "rel = entry.relative_path"
    fixed = 'rel = entry.relative_path.replace("\\\\", "/")'
    if needle in txt and fixed not in txt:
        txt = txt.replace(needle, fixed)
    # _is_structural_exclusion 첫 줄 정규화(함수 시그니처 뒤에 삽입)
    if "_is_structural_exclusion" in txt and 'rel = rel.replace("\\\\", "/")' not in txt:
        import re
        txt = re.sub(
            r"(def _is_structural_exclusion\([^)]*\)\s*(?:->[^\:]+)?:\n)",
            r'\1    rel = rel.replace("\\\\", "/")\n',
            txt, count=1,
        )
    if txt != orig:
        _BOM.write_text(txt, encoding="utf-8")
        return True
    return True
