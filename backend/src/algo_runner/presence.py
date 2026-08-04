"""아주 가벼운 접속 추적 — 인증 요청마다 username 의 last-seen 을 기록하고,
최근 N초 내 활동한 사용자를 '현재 접속 중'으로 센다(별도 세션/하트비트 인프라 없이)."""
from __future__ import annotations

import threading
import time

_seen: dict[str, float] = {}
_lock = threading.Lock()


def touch(username: str) -> None:
    if not username:
        return
    with _lock:
        _seen[username] = time.time()


def active(window: float = 300.0) -> list[str]:
    """최근 window 초 내 활동한 사용자 목록."""
    now = time.time()
    with _lock:
        return sorted(u for u, t in _seen.items() if now - t <= window)
