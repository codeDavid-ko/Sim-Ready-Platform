"""content-agents 단일 에이전트(material/physics/texture) 공용 오케스트레이터.

Windows 백엔드 -> wsl.exe -> ~/content-agents/run_agent.sh <agent> 실행 후
output.usd / output.usdz / result.json 회수·등록. content_physics / content_texture
핸들러가 얇게 호출한다. (content_material 은 자체 handler 유지.)
"""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path
from typing import Any

_DISTRO = "Ubuntu-24.04"
_RUNS = Path(__file__).resolve().parents[3] / "_ca_runs"  # backend/_ca_runs
_SUPPORTED = {".usd", ".usda", ".usdc", ".usdz"}
_TIMEOUT = 1200


def _to_wsl(p: Path) -> str:
    s = str(p)
    return f"/mnt/{s[0].lower()}{s[2:].replace(chr(92), '/')}"


def run_agent_card(agent: str, file_bytes: bytes | None, file_name: str | None, ctx: Any) -> dict[str, Any]:
    if not file_bytes:
        raise ValueError("USD 파일을 업로드하세요.")
    ext = Path(file_name or "").suffix.lower()
    if ext not in _SUPPORTED:
        raise ValueError(f"USD 형식만 지원합니다(.usd/.usda/.usdc/.usdz). 받은: {ext or '?'}")

    rid = uuid.uuid4().hex[:12]
    rundir = _RUNS / rid
    rundir.mkdir(parents=True, exist_ok=True)
    inp = rundir / f"input{ext}"
    inp.write_bytes(file_bytes)

    cmd = [
        "wsl.exe", "-d", _DISTRO, "bash", "-lc",
        f"bash ~/content-agents/run_agent.sh {agent} '{_to_wsl(inp)}' '{_to_wsl(rundir)}'",
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{agent} 파이프라인 시간 초과({_TIMEOUT}s).") from None
    except FileNotFoundError:
        raise RuntimeError("wsl.exe 를 찾을 수 없습니다(WSL2 필요).") from None

    def _read(name: str) -> str:
        f = rundir / name
        return f.read_text(encoding="utf-8", errors="replace") if f.exists() else ""

    status = (_read("status.txt") or "NO_STATUS").strip()
    try:
        result = json.loads(_read("result.json") or "{}")
    except ValueError:
        result = {}
    log_tail = "\n".join(_read("run.log").splitlines()[-15:])

    stem = Path(file_name or "asset").stem
    out_usd = rundir / "output.usd"
    out_usdz = rundir / "output.usdz"
    asset = usdz_asset = preview = None
    if out_usd.exists() and ctx is not None:
        asset = ctx.register_asset(
            stem, f"{stem}_{agent}.usd", out_usd.read_bytes(),
            {"source": file_name, "agent": agent, **result},
        ) if False else ctx.register_asset(
            display_name=stem, filename=f"{stem}_{agent}.usd",
            data=out_usd.read_bytes(),
            meta={"source": file_name, "agent": agent},
        )
        if out_usdz.exists():
            try:
                usdz_asset = ctx.register_asset(
                    display_name=f"{stem} (usdz)", filename=f"{stem}.usdz",
                    data=out_usdz.read_bytes(), meta={"stage": "usdz", "self_contained": True},
                )
            except Exception:  # noqa: BLE001
                usdz_asset = None
        # PBR GLB 미리보기 (재질 바인딩 있으면 입력 지오메트리 + bindings 로)
        mats = result.get("materials", {})
        if mats:
            try:
                from .preview import glb_from_usd_with_bindings
                preview = ctx.register_asset(
                    display_name=f"{stem} (preview)", filename=f"{stem}_preview.glb",
                    data=glb_from_usd_with_bindings(file_bytes, mats), meta={"stage": "preview"},
                )
            except Exception:  # noqa: BLE001
                preview = None
    if asset is None:
        raise RuntimeError(f"출력 USD 생성 실패. status={status}.\n로그:\n{log_tail}")

    return {
        "agent": agent,
        "status": status,
        "materials": result.get("materials", {}),
        "physics": result.get("physics", {}),
        "asset": asset,
        "usdz_asset": usdz_asset,
        "preview": preview,
        "log_tail": log_tail,
    }
