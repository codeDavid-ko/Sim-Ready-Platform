"""content-material 워크플로우 — NVIDIA content-agents Material 파이프라인을
WSL2에서 오케스트레이션.

Windows 백엔드 → wsl.exe → ~/content-agents/run_one.sh (Warp 멀티뷰 렌더 +
구독 Claude VLM via anthropic_oauth 백엔드) → 재질 바인딩된 USD 회수.

material-usd 워크플로우와 달리, 알고리즘은 NVIDIA content-agents가 수행하고
이 핸들러는 얇은 오케스트레이터다(파일 전달 + WSL 실행 + 결과 회수).
"""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path
from typing import Any

_DISTRO = "Ubuntu-24.04"
_RUNS = Path(__file__).resolve().parents[4] / "_ca_runs"  # backend/_ca_runs
_SUPPORTED = {".usd", ".usda", ".usdc", ".usdz"}
_TIMEOUT = 900  # seconds (full pipeline incl. per-prim VLM calls)


def _to_wsl(p: Path) -> str:
    """C:\\X\\Y -> /mnt/c/X/Y"""
    s = str(p)
    drive = s[0].lower()
    rest = s[2:].replace("\\", "/")
    return f"/mnt/{drive}{rest}"


def run(
    params: dict[str, Any],
    file_bytes: bytes | None = None,
    file_name: str | None = None,
    ctx: Any = None,
) -> dict[str, Any]:
    if not file_bytes:
        raise ValueError("USD 파일을 업로드하세요.")
    ext = Path(file_name or "").suffix.lower()
    if ext not in _SUPPORTED:
        raise ValueError(f"USD 형식만 지원합니다(.usd/.usda/.usdc/.usdz). 받은: {ext or '?'}")

    run_id = uuid.uuid4().hex[:12]
    rundir = _RUNS / run_id
    rundir.mkdir(parents=True, exist_ok=True)
    in_path = rundir / f"input{ext}"
    in_path.write_bytes(file_bytes)

    wsl_in = _to_wsl(in_path)
    wsl_out = _to_wsl(rundir)
    cmd = [
        "wsl.exe", "-d", _DISTRO, "bash", "-lc",
        f"bash ~/content-agents/run_one.sh '{wsl_in}' '{wsl_out}'",
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"파이프라인 시간 초과({_TIMEOUT}s).") from None
    except FileNotFoundError:
        raise RuntimeError("wsl.exe 를 찾을 수 없습니다(WSL2 필요).") from None

    status_f = rundir / "status.txt"
    status = status_f.read_text(encoding="utf-8", errors="replace").strip() if status_f.exists() else "NO_STATUS"

    bindings: dict[str, Any] = {}
    bf = rundir / "bindings.json"
    if bf.exists():
        try:
            bindings = json.loads(bf.read_text(encoding="utf-8"))
        except ValueError:
            pass

    log_tail = ""
    lf = rundir / "run.log"
    if lf.exists():
        log_tail = "\n".join(
            lf.read_text(encoding="utf-8", errors="replace").splitlines()[-15:]
        )

    out_usd = rundir / "output.usd"
    asset = None
    preview = None
    if out_usd.exists() and ctx is not None:
        stem = Path(file_name or "asset").stem
        usd_data = out_usd.read_bytes()
        asset = ctx.register_asset(
            display_name=stem,
            filename=f"{stem}_material.usd",
            data=usd_data,
            meta={
                "source": file_name,
                "engine": "nvidia-content-agents",
                "bindings": bindings.get("bindings", {}),
            },
        )
        # 브라우저 3D 미리보기용 PBR GLB. content-agents 출력은 입력을 서브레이어로
        # 참조해 자기완결이 아니므로, 입력 지오메트리 + 추론 bindings 로 PBR 근사.
        try:
            from ..preview import glb_from_usd_with_bindings

            preview = ctx.register_asset(
                display_name=f"{stem} (preview)",
                filename=f"{stem}_preview.glb",
                data=glb_from_usd_with_bindings(file_bytes, bindings.get("bindings", {})),
                meta={"stage": "preview"},
            )
        except Exception:  # noqa: BLE001 -- preview is best-effort
            preview = None
    if asset is None:
        raise RuntimeError(
            f"출력 USD가 생성되지 않았습니다. status={status}.\n로그 마지막:\n{log_tail}"
        )

    return {
        "engine": "NVIDIA content-agents (Material) · Warp 멀티뷰 렌더 · 구독 Claude VLM",
        "status": status,
        "bindings": bindings.get("bindings", {}),
        "asset": asset,
        "preview": preview,
        "log_tail": log_tail,
    }
