"""Isaac Sim 6.0 멀티앵글 RTX 렌더 러너 (Windows).

자기완결 USD(vMaterials MDL)를 Isaac Sim python.bat 로 헤드리스 렌더해 여러 각도
PNG 를 만든다. material-usd 결과 USD 의 "진짜 MDL 룩"을 보여주기 위함.
(content-agents 출력은 WSL 서브레이어 참조라 비자기완결 → 여기선 미지원.)
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from .. import jobs

_PYTHON_BAT = r"C:\Omniverse\IsaacSim\IsaacSim\_build\windows-x86_64\release\python.bat"
_GUI_BAT = r"C:\Omniverse\IsaacSim\IsaacSim\_build\windows-x86_64\release\isaac-sim.bat"
_SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "isaac_render.py"
_TT_SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "isaac_turntable.py"
_GUI_TT_STARTUP = Path(__file__).resolve().parents[4] / "scripts" / "isaac_gui_turntable_startup.py"

# Isaac Sim / Omniverse Kit 은 한 머신에서 인스턴스 2개를 동시에 못 돌린다(SimulationApp
# 충돌 → "Recursive unloadAllPlugins / No stage found"). 모든 렌더를 이 락으로 직렬화한다
# (예: 비교 카드에서 ours + NVIDIA 스핀을 함께 누르면 두 Isaac 프로세스가 충돌). 잡 스레드들이
# 이 락을 두고 줄서서 한 번에 하나만 렌더한다.
_ISAAC_LOCK = threading.Lock()


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


def _crate_safe(usd_path: str) -> tuple[str, str | None]:
    """Isaac 가 열 수 있는 경로 보장. 바이너리 crate(PXR-USDC)인데 확장자가 .usd 계열이 아니면
    (.usda/.usdz 등) pxr 가 텍스트/zip 으로 파싱하려다 'No stage found' 로 죽는다 → .usd
    임시본으로 복사해 넘긴다. 반환: (열_경로, 정리할_임시경로 or None)."""
    try:
        with open(usd_path, "rb") as f:
            magic = f.read(8)
    except OSError:
        return usd_path, None
    if magic == b"PXR-USDC" and Path(usd_path).suffix.lower() not in (".usd", ".usdc"):
        fd, tmp = tempfile.mkstemp(suffix=".usd")
        os.close(fd)
        shutil.copyfile(usd_path, tmp)
        return tmp, tmp
    return usd_path, None


def render_turntable(usd_path: str, frames: int = 48, res: int = 720, timeout: int = 900) -> bytes:
    """USD/USDZ -> 360° 회전 mp4 bytes (Isaac RTX 프레임 렌더 + ffmpeg). 실패 시 RuntimeError."""
    if not isaac_available():
        raise RuntimeError("Isaac Sim(python.bat) 또는 렌더 스크립트를 찾을 수 없습니다.")
    ff = _find_ffmpeg()
    if not ff:
        raise RuntimeError("ffmpeg 를 찾을 수 없습니다(mp4 생성 불가).")
    usd_path, _tmp = _crate_safe(usd_path)
    out = tempfile.mkdtemp(prefix="isaac_turntable_")
    mp4 = os.path.join(out, "turntable.mp4")
    cmd = [
        _PYTHON_BAT, str(_SCRIPT),
        "--usd", usd_path, "--out", out,
        "--views", str(frames), "--res", str(res), "--settle", "14",
        "--mp4", mp4, "--ffmpeg", ff, "--fps", "20",
    ]
    try:
        with _ISAAC_LOCK:  # Isaac 인스턴스 충돌 방지 — 한 번에 하나만
            proc = jobs.run(cmd, timeout=timeout)
        if not os.path.exists(mp4):
            tail = (proc.stdout or "")[-800:] + (proc.stderr or "")[-300:]
            raise RuntimeError(f"턴테이블 mp4 생성 실패. 로그:\n{tail}")
        with open(mp4, "rb") as f:
            return f.read()
    finally:
        if _tmp:
            try:
                os.remove(_tmp)
            except OSError:
                pass


def render_turntable_video(
    usd_path: str, *, turns: float = 1.0, spin_dir: int = 1, seconds: float = 12.0,
    fps: int = 24, res: int = 1080, zoom: bool = False, zoom_mult: float = 2.0,
    zoom_in: float = 0.46, zoom_out: float = 0.66,
    motors: bool = False, motor_mode: str = "hold", motor_deg: float = 90.0,
    clean: bool = True, motor_targets: str = "", settle: int = 18, timeout: int = 1800,
    quality: str = "standard", ptspp: int = 64,
) -> bytes:
    """USD → 턴테이블 mp4 bytes (회전 + 옵션 줌/모터). scripts/isaac_turntable.py 호출.

    Isaac 1 인스턴스 직렬화(_ISAAC_LOCK) + 취소 가능(jobs.run). 실패 시 RuntimeError.
    """
    if not turntable_available():
        raise RuntimeError("Isaac Sim(python.bat) 또는 턴테이블 렌더 스크립트를 찾을 수 없습니다.")
    ff = _find_ffmpeg()
    if not ff:
        raise RuntimeError("ffmpeg 를 찾을 수 없습니다(mp4 생성 불가).")
    out = tempfile.mkdtemp(prefix="isaac_tt_")
    mp4 = os.path.join(out, "turntable.mp4")
    cmd = [
        _PYTHON_BAT, str(_TT_SCRIPT),
        "--usd", usd_path, "--out", out,
        "--turns", str(turns), "--spin-dir", str(spin_dir),
        "--seconds", str(seconds), "--fps", str(fps), "--res", str(res),
        "--motor-mode", motor_mode, "--motor-deg", str(motor_deg),
        "--zoom-mult", str(zoom_mult), "--settle", str(settle),
        "--zoom-in", str(zoom_in), "--zoom-out", str(zoom_out),
        "--quality", (quality if quality in ("standard", "hq", "pt") else "standard"),
        "--ptspp", str(ptspp),
        "--mp4", mp4, "--ffmpeg", ff,
    ]
    if zoom:
        cmd.append("--zoom")
    if motors:
        cmd.append("--motors")
    if clean:
        cmd.append("--clean")
    if motor_targets:
        cmd += ["--motor-targets", motor_targets]

    # 진행률 워처: out 폴더의 프레임 수 + _phase.txt 를 폴링해 잡 진행률로 보고(실제 n/total).
    total = int(round(fps * seconds)) + 1
    jid = jobs.current_job()
    _stop = threading.Event()

    def _watch() -> None:
        import time as _t
        phase_f = os.path.join(out, "_phase.txt")
        while not _stop.is_set():
            try:
                n = len(glob.glob(os.path.join(out, "frame_*.png")))
                ph = ""
                if os.path.exists(phase_f):
                    with open(phase_f, encoding="utf-8") as f:
                        ph = f.read().strip()
                if jid:
                    jobs.set_progress(jid, {"phase": ph, "frame": n, "total": total})
            except Exception:  # noqa: BLE001
                pass
            _stop.wait(2.0)

    watcher = threading.Thread(target=_watch, daemon=True)
    with _ISAAC_LOCK:  # Isaac 인스턴스 충돌 방지 — 한 번에 하나만
        watcher.start()
        try:
            proc = jobs.run(cmd, timeout=timeout)
        finally:
            _stop.set()
    if not os.path.exists(mp4):
        tail = (proc.stdout or "")[-1200:] + (proc.stderr or "")[-400:]
        raise RuntimeError(f"턴테이블 영상 생성 실패. 로그:\n{tail}")
    with open(mp4, "rb") as f:
        return f.read()


def turntable_available() -> bool:
    return os.path.isfile(_PYTHON_BAT) and _TT_SCRIPT.is_file()


def gui_turntable_available() -> bool:
    """GUI 화면녹화 모드 가용성 — GUI 런처 + startup 스크립트 + ffmpeg(ddagrab) 필요.
    추가로 대화형 데스크톱 세션이어야 실제 캡처가 됨(여기선 바이너리 존재만 확인)."""
    return os.path.isfile(_GUI_BAT) and _GUI_TT_STARTUP.is_file() and bool(_find_ffmpeg())


def record_gui_turntable(
    usd_path: str, *, seconds: float = 12.0, fps: int = 30, turns: float = 1.0,
    spin_dir: int = 1, monitor: int = 0, clean: bool = True,
    zoom: bool = False, zoom_mult: float = 2.0, zoom_in: float = 0.46, zoom_out: float = 0.66,
    motors: bool = False, motor_mode: str = "hold", motor_deg: float = 90.0, motor_targets: str = "",
    boot_timeout: int = 300, lead: float = 0.4,
) -> bytes:
    """Isaac Sim GUI(앱 화면 통째)를 띄워 턴테이블 회전시키고 ffmpeg ddagrab 으로 화면 녹화 → mp4.

    설계문서 §4 흐름: GUI 앱(--exec startup) 실행 → ready 파일 대기 → ddagrab N초 녹화 → 프로세스 트리 종료.
    Isaac 1 인스턴스 직렬화(_ISAAC_LOCK), jobs 로 등록(취소 시 트리 kill).
    """
    import time

    if not gui_turntable_available():
        raise RuntimeError("GUI 녹화 모드 불가 — Isaac GUI 런처 / startup 스크립트 / ffmpeg(ddagrab) 확인.")
    ff = _find_ffmpeg()
    open_path, tmp = _crate_safe(usd_path)
    out = tempfile.mkdtemp(prefix="isaac_gui_tt_")
    mp4 = os.path.join(out, "gui_turntable.mp4")
    ready = os.path.join(out, "ready.txt")
    guilog = os.path.join(out, "gui.log")

    def _logtail(nchars: int = 1500) -> str:
        try:
            with open(guilog, encoding="utf-8", errors="replace") as f:
                return f.read()[-nchars:]
        except OSError:
            return ""

    env = os.environ.copy()
    env.update({
        "SR_GUI_USD": open_path, "SR_GUI_SECONDS": str(seconds), "SR_GUI_FPS": str(fps),
        "SR_GUI_TURNS": str(turns), "SR_GUI_SPIN": str(spin_dir),
        "SR_GUI_READY": ready, "SR_GUI_CLEAN": "1" if clean else "0",
        "SR_GUI_ZOOM": "1" if zoom else "0", "SR_GUI_ZOOMMULT": str(zoom_mult),
        "SR_GUI_ZOOMIN": str(zoom_in), "SR_GUI_ZOOMOUT": str(zoom_out),
        "SR_GUI_MOTORS": "1" if motors else "0", "SR_GUI_MOTORMODE": motor_mode,
        "SR_GUI_MOTORDEG": str(motor_deg), "SR_GUI_MOTORTARGETS": motor_targets or "",
    })
    gui_cmd = [
        _GUI_BAT, "--exec", str(_GUI_TT_STARTUP),
        "--/app/window/fullscreen=true", "--/app/window/hideUi=false", "--no-ros-env",
    ]

    logf = open(guilog, "w", encoding="utf-8", errors="replace")
    with _ISAAC_LOCK:
        gui = subprocess.Popen(gui_cmd, env=env, stdout=logf, stderr=subprocess.STDOUT, text=True)
        jobs.register_proc(gui)
        try:
            # 1) ready 대기 (GUI 부팅 + 스테이지 로드 + RTX 수렴)
            t0 = time.time()
            while not os.path.exists(ready):
                if gui.poll() is not None:
                    raise RuntimeError(f"GUI Isaac 프로세스가 준비 전에 종료됨.\n{_logtail()}")
                if jobs.is_cancelled():
                    raise jobs.JobCancelled()
                if time.time() - t0 > boot_timeout:
                    raise RuntimeError(f"GUI 준비 시간 초과({boot_timeout}s) — 부팅/디스플레이 세션 확인.\n{_logtail()}")
                time.sleep(1.0)
            time.sleep(lead)  # 살짝 여유(첫 프레임 안정)

            # 2) ffmpeg ddagrab 화면 녹화 (주 모니터=Isaac 풀스크린)
            rec_cmd = [
                ff, "-y", "-f", "lavfi",
                "-i", f"ddagrab=output_idx={int(monitor)}:framerate={int(fps)}:draw_mouse=false",
                "-t", str(float(seconds)),
                "-vf", "hwdownload,format=bgra,format=yuv420p",
                "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                mp4,
            ]
            rec = jobs.run(rec_cmd, timeout=int(seconds * 3 + 60))
            if rec.returncode != 0 and not os.path.exists(mp4):
                tail = (rec.stdout or "")[-800:]
                raise RuntimeError(f"화면 녹화(ffmpeg ddagrab) 실패:\n{tail}")
        finally:
            jobs.unregister_proc(gui)
            try:
                jobs.kill_tree(gui.pid)
            except Exception:  # noqa: BLE001
                pass
            try:
                logf.close()
            except Exception:  # noqa: BLE001
                pass
            if tmp:
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    if not os.path.exists(mp4):
        raise RuntimeError(f"GUI 녹화 결과 mp4 가 생성되지 않았습니다.\n{_logtail()}")
    with open(mp4, "rb") as f:
        return f.read()


def render_spin_frames(
    usd_path: str, frames: int = 36, res: int = 540, elevations: int = 1, timeout: int = 900
) -> tuple[list[bytes], int, int]:
    """USD/USDZ -> 스핀 프레임 png 바이트 + (rows, cols).

    브라우저 'object-movie' 스핀 뷰어용 — 마우스 드래그로 프레임을 넘겨 회전.
    실제 RTX(MDL/MaterialX) 룩 그대로. elevations>1 이면 고도×방위각 격자
    (view_<e>_<a>.png) → (행=고도, 열=방위각) 순서로 평탄화해 돌려준다.
    반환: (data, rows, cols). data[row*cols + col] 가 (고도 row, 방위 col) 프레임."""
    if not isaac_available():
        raise RuntimeError("Isaac Sim(python.bat) 또는 렌더 스크립트를 찾을 수 없습니다.")
    usd_path, _tmp = _crate_safe(usd_path)
    out = tempfile.mkdtemp(prefix="isaac_spin_")
    cmd = [
        _PYTHON_BAT, str(_SCRIPT),
        "--usd", usd_path, "--out", out,
        "--views", str(frames), "--res", str(res), "--settle", "10",
        "--elevations", str(max(1, int(elevations))),
    ]
    try:
        with _ISAAC_LOCK:  # Isaac 인스턴스 충돌 방지 — 한 번에 하나만
            proc = jobs.run(cmd, timeout=timeout)
    finally:
        if _tmp:
            try:
                os.remove(_tmp)
            except OSError:
                pass

    def _key(p: str) -> tuple[int, int]:
        # view_<e>_<a> → (e,a);  view_<i> → (0,i)
        parts = os.path.splitext(os.path.basename(p))[0].split("_")[1:]
        try:
            nums = [int(x) for x in parts]
        except ValueError:
            return (0, 0)
        return (nums[0], nums[1]) if len(nums) >= 2 else (0, nums[0])

    paths = sorted(glob.glob(os.path.join(out, "view_*.png")), key=_key)
    rows = len({_key(p)[0] for p in paths}) or 1
    cols = (len(paths) // rows) if rows else len(paths)
    data = []
    for p in paths:
        with open(p, "rb") as f:
            data.append(f.read())
    if not data:
        tail = (proc.stdout or "")[-800:] + (proc.stderr or "")[-400:]
        raise RuntimeError(f"스핀 프레임 렌더 산출 없음. 로그:\n{tail}")
    return data, rows, cols


def render_usd_multiangle(
    usd_path: str, views: int = 6, res: int = 720, timeout: int = 600, lights: str = "auto",
    fit: float = 1.0, dome: float = 0.0,
) -> list[tuple[str, bytes]]:
    """USD -> [(filename, png_bytes)] 여러 각도. 실패 시 RuntimeError.
    lights: 'auto'(스테이지/디폴트) 또는 'thumbnail'(NVIDIA RectLight+Dome 리그).
    fit: 카메라 거리 배수(1.0=기존). 길쭉한 자산(볼라드·시그널타워)은 기본 거리에서
         위아래가 잘리므로 1.4~1.6 을 주면 전체가 들어온다.
    dome: >0 이면 DomeLight(앰비언트)를 그 intensity 로 보장 — **배경색을 통일**한다.
          Dome 이 없는 자산은 배경이 검게, 있는 자산은 회색으로 나와 썸네일이 들쭉날쭉해진다."""
    if not isaac_available():
        raise RuntimeError("Isaac Sim(python.bat) 또는 렌더 스크립트를 찾을 수 없습니다.")
    out = tempfile.mkdtemp(prefix="isaac_render_")
    cmd = [
        _PYTHON_BAT, str(_SCRIPT),
        "--usd", usd_path, "--out", out,
        "--views", str(views), "--res", str(res), "--lights", lights,
        "--fit", str(float(fit)), "--dome", str(float(dome)),
    ]
    with _ISAAC_LOCK:  # Isaac 인스턴스 충돌 방지 — 한 번에 하나만
        proc = jobs.run(cmd, timeout=timeout)
    imgs: list[tuple[str, bytes]] = []
    for p in sorted(glob.glob(os.path.join(out, "view_*.png"))):
        with open(p, "rb") as f:
            imgs.append((os.path.basename(p), f.read()))
    if not imgs:
        tail = (proc.stdout or "")[-800:] + (proc.stderr or "")[-400:]
        raise RuntimeError(f"Isaac 렌더 산출 이미지 없음. 로그:\n{tail}")
    return imgs
