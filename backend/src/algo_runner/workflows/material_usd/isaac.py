"""Isaac Sim 6.0 멀티앵글 RTX 렌더 러너 (Windows).

자기완결 USD(vMaterials MDL)를 Isaac Sim python.bat 로 헤드리스 렌더해 여러 각도
PNG 를 만든다. material-usd 결과 USD 의 "진짜 MDL 룩"을 보여주기 위함.
(content-agents 출력은 WSL 서브레이어 참조라 비자기완결 → 여기선 미지원.)
"""

from __future__ import annotations

import glob
import os
import subprocess
import tempfile
from pathlib import Path

_PYTHON_BAT = r"C:\Omniverse\IsaacSim\IsaacSim\_build\windows-x86_64\release\python.bat"
_SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "isaac_render.py"


def isaac_available() -> bool:
    return os.path.isfile(_PYTHON_BAT) and _SCRIPT.is_file()


def _find_ffmpeg() -> str:
    import shutil

    f = shutil.which("ffmpeg")
    if f:
        return f
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        for p in glob.glob(
            os.path.join(local, "Microsoft", "WinGet", "Packages", "Gyan.FFmpeg*", "**", "ffmpeg.exe"),
            recursive=True,
        ):
            return p
    return ""


def render_turntable(usd_path: str, frames: int = 48, res: int = 720, timeout: int = 900) -> bytes:
    """USD/USDZ -> 360° 회전 mp4 bytes (Isaac RTX 프레임 렌더 + ffmpeg). 실패 시 RuntimeError."""
    if not isaac_available():
        raise RuntimeError("Isaac Sim(python.bat) 또는 렌더 스크립트를 찾을 수 없습니다.")
    ff = _find_ffmpeg()
    if not ff:
        raise RuntimeError("ffmpeg 를 찾을 수 없습니다(mp4 생성 불가).")
    out = tempfile.mkdtemp(prefix="isaac_turntable_")
    mp4 = os.path.join(out, "turntable.mp4")
    cmd = [
        _PYTHON_BAT, str(_SCRIPT),
        "--usd", usd_path, "--out", out,
        "--views", str(frames), "--res", str(res), "--settle", "14",
        "--mp4", mp4, "--ffmpeg", ff, "--fps", "20",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if not os.path.exists(mp4):
        tail = (proc.stdout or "")[-800:] + (proc.stderr or "")[-300:]
        raise RuntimeError(f"턴테이블 mp4 생성 실패. 로그:\n{tail}")
    with open(mp4, "rb") as f:
        return f.read()


def render_usd_multiangle(
    usd_path: str, views: int = 6, res: int = 720, timeout: int = 600
) -> list[tuple[str, bytes]]:
    """USD -> [(filename, png_bytes)] 여러 각도. 실패 시 RuntimeError."""
    if not isaac_available():
        raise RuntimeError("Isaac Sim(python.bat) 또는 렌더 스크립트를 찾을 수 없습니다.")
    out = tempfile.mkdtemp(prefix="isaac_render_")
    cmd = [
        _PYTHON_BAT, str(_SCRIPT),
        "--usd", usd_path, "--out", out,
        "--views", str(views), "--res", str(res),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    imgs: list[tuple[str, bytes]] = []
    for p in sorted(glob.glob(os.path.join(out, "view_*.png"))):
        with open(p, "rb") as f:
            imgs.append((os.path.basename(p), f.read()))
    if not imgs:
        tail = (proc.stdout or "")[-800:] + (proc.stderr or "")[-400:]
        raise RuntimeError(f"Isaac 렌더 산출 이미지 없음. 로그:\n{tail}")
    return imgs
