"""Cloudflare 임시 터널 — 외부 공개 주소를 켜고/끈다. 백엔드 재시작에도 유지(detached).

프런트 포트 하나만 노출(same-origin 프록시로 API 까지 동작). 무료 임시주소(*.trycloudflare.com).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

_URL_RE = re.compile(r"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com")
_STATE_DIR = Path.home() / ".algo-runner"


def resolve_cloudflared(explicit: str = "") -> str | None:
    if explicit and Path(explicit).is_file():
        return explicit
    found = shutil.which("cloudflared")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        link = Path(local) / "Microsoft" / "WinGet" / "Links" / "cloudflared.exe"
        if link.is_file():
            return str(link)
        for exe in (Path(local) / "Microsoft" / "WinGet" / "Packages").glob(
            "Cloudflare.cloudflared_*/cloudflared.exe"
        ):
            return str(exe)
    return None


@dataclass
class TunnelStatus:
    running: bool
    url: str | None
    error: str | None
    installed: bool


class TunnelManager:
    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._url: str | None = None
        self._error: str | None = None
        self._lock = threading.Lock()

    def _state(self) -> Path:
        return _STATE_DIR / "tunnel.json"

    def _log(self) -> Path:
        return _STATE_DIR / "tunnel.log"

    def _save(self, pid: int, url: str | None) -> None:
        try:
            _STATE_DIR.mkdir(parents=True, exist_ok=True)
            self._state().write_text(json.dumps({"pid": pid, "url": url}), encoding="utf-8")
        except OSError:
            pass

    def _load(self) -> dict:
        try:
            return json.loads(self._state().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if not pid:
            return False
        try:
            if os.name == "nt":
                out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                                     capture_output=True, text=True, timeout=10)
                return str(pid) in out.stdout
            os.kill(pid, 0)
            return True
        except (OSError, subprocess.SubprocessError):
            return False

    def _alive(self) -> bool:
        if self._proc is not None and self._proc.poll() is None:
            return True
        st = self._load()
        pid = st.get("pid")
        if pid and self._pid_alive(int(pid)):
            if self._url is None:
                self._url = st.get("url")
            return True
        return False

    def status(self, cloudflared_path: str = "") -> TunnelStatus:
        alive = self._alive()
        return TunnelStatus(running=alive, url=self._url if alive else None,
                            error=self._error, installed=resolve_cloudflared(cloudflared_path) is not None)

    def start(self, port: int, cloudflared_path: str = "") -> TunnelStatus:
        with self._lock:
            if self._alive():
                return self.status(cloudflared_path)
            exe = resolve_cloudflared(cloudflared_path)
            if not exe:
                self._error = "cloudflared 가 설치되어 있지 않습니다."
                return self.status(cloudflared_path)
            self._url = None
            self._error = None
            _STATE_DIR.mkdir(parents=True, exist_ok=True)
            logf = open(self._log(), "w", encoding="utf-8")  # noqa: SIM115
            flags = 0
            if os.name == "nt":
                flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            try:
                self._proc = subprocess.Popen(
                    [exe, "tunnel", "--url", f"http://localhost:{port}", "--no-autoupdate"],
                    stdout=logf, stderr=subprocess.STDOUT, creationflags=flags,
                )
            except OSError as exc:
                self._error = f"실행 실패: {exc}"
                self._proc = None
                return self.status(cloudflared_path)
            self._save(self._proc.pid, None)
            threading.Thread(target=self._read_log, daemon=True).start()
            return self.status(cloudflared_path)

    def _read_log(self) -> None:
        for _ in range(60):
            try:
                text = self._log().read_text(encoding="utf-8", errors="ignore")
            except OSError:
                text = ""
            m = _URL_RE.search(text)
            if m:
                self._url = m.group(0)
                if self._proc is not None:
                    self._save(self._proc.pid, self._url)
                return
            if self._proc is not None and self._proc.poll() is not None:
                break
            time.sleep(1)
        if self._url is None and self._error is None:
            self._error = "터널 주소를 얻지 못했습니다."

    def stop(self, cloudflared_path: str = "") -> TunnelStatus:
        with self._lock:
            if self._proc is not None:
                try:
                    self._proc.terminate()
                    try:
                        self._proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self._proc.kill()
                except OSError:
                    pass
            pid = self._load().get("pid")
            if pid and self._pid_alive(int(pid)):
                try:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=10)
                    else:
                        os.kill(int(pid), 15)
                except (OSError, subprocess.SubprocessError):
                    pass
            self._proc = None
            self._url = None
            try:
                self._state().unlink(missing_ok=True)
            except OSError:
                pass
            return self.status(cloudflared_path)


_MANAGER = TunnelManager()


def get_tunnel_manager() -> TunnelManager:
    return _MANAGER
