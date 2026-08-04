"""§11a 프리플라이트 자가검사 — 이 카드가 하드코딩한 SPEC 가정이 실제 NVIDIA 소스와 여전히 일치하는지,
_bom.py 패치가 적용돼 있는지, 텍스처 검증용 Pillow 가 검증기 venv 에 있는지 확인.
verify_against_nvidia_sources.py 의 소스대조(A/B) 부분을 카드용으로 이식(PDF/사전빌드 패키지 제외).
실패하면 카드가 가정과 어긋난 것 → 사용자에게 경고."""
from __future__ import annotations

import subprocess
from typing import Any

from . import env


def _read(p) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def run_preflight() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def chk(name: str, ok: bool, source: str) -> None:
        checks.append({"name": name, "passed": bool(ok), "source": source})

    docs = env.DOCS
    toml = docs / "profiles" / "profiles.toml"
    t = _read(toml)
    for prof in ("Prop-Robotics-Neutral", "Prop-Robotics-Physx", "Prop-Robotics-Isaac"):
        chk(f"profiles.toml 에 [{prof}] 존재", f"[{prof}]" in t, "profiles.toml")
    chk("프롭 프로파일이 FET006_BASE_MDL 사용", "FET006_BASE_MDL" in t, "profiles.toml")
    chk("Isaac 프로파일 FET100 포함", "FET100" in t, "profiles.toml")

    gp = docs / "capabilities" / "physics_bodies" / "physics_graspable" / "validation.py"
    chk("GSP.001: grasp prim 이름 startswith('grasp_identifier')",
        'startswith("grasp_identifier")' in _read(gp), str(gp.name))

    srp = docs / "capabilities" / "core" / "sim_ready" / "validation.py"
    sr = _read(srp)
    chk("SR.001 필수 키(asset_name/usd_date_generated/SimReady_Metadata)",
        all(k in sr for k in ('"asset_name"', '"usd_date_generated"', '"SimReady_Metadata"')), "sim_ready/validation.py")
    chk("SR.002 썸네일 경로 .thumbs/256x256", ".thumbs" in sr and "256x256" in sr, "sim_ready/validation.py")

    mp = docs / "capabilities" / "visualization" / "materials" / "validation.py"
    mm = _read(mp)
    chk("VM.MDL.001: MDL 경로 './' 시작 + 존재 검사", "should start with './'" in mm and "does not exist" in mm, "materials/validation.py")

    php = docs / "capabilities" / "physics_bodies" / "physics_colliders" / "validation.py"
    chk("COL.001: PhysX 충돌 근사 'sdf' 요구", "instead of 'sdf'" in _read(php), "physics_colliders/validation.py")

    aap = docs / "capabilities" / "core" / "atomic_asset" / "requirements" / "anchored-asset-paths.md"
    aa = _read(aap)
    chk("AA.001: 에셋 경로 앵커드(./ 또는 ../) 요구", "./" in aa and "anchor" in aa.lower(), "anchored-asset-paths.md")

    # _bom.py 패치(없으면 적용) + pillow(검증기 venv)
    patched = env.ensure_bom_patch()
    bom = _read(env._BOM)
    chk("_bom.py Windows 경로 패치 적용됨", 'replace("\\\\", "/")' in bom and patched, "_bom.py")
    try:
        r = subprocess.run([str(env.VENV_PY), "-c", "import PIL"], capture_output=True, text=True, env=env.subprocess_env())
        chk("검증기 venv 에 Pillow 설치(VM.TEX.001 크기검사용)", r.returncode == 0, "simready .venv")
    except Exception as e:  # noqa: BLE001
        chk("검증기 venv 에 Pillow 설치", False, str(e))

    npass = sum(1 for c in checks if c["passed"])
    return {"ok": npass == len(checks), "passed": npass, "total": len(checks), "checks": checks}
