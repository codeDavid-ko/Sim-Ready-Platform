"""아주 가벼운 인메모리 잡 스토어 — 긴 워크플로우(content-material, compare)를
백그라운드 스레드로 돌리고 폴링으로 결과를 받는다.

긴 동기 요청이 (Next 개발 프록시 등에서) 타임아웃되는 문제를 피하기 위함.
프로세스 메모리에만 저장(재시작 시 사라짐) — 데모/단일 인스턴스용.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any, Callable

_jobs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def submit(fn: Callable[[], Any]) -> str:
    """fn 을 데몬 스레드로 실행하고 job_id 반환."""
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        _jobs[job_id] = {"status": "running", "result": None, "error": None}

    def _run() -> None:
        try:
            result = fn()
            with _lock:
                _jobs[job_id] = {"status": "done", "result": result, "error": None}
        except Exception as exc:  # noqa: BLE001
            with _lock:
                _jobs[job_id] = {"status": "error", "result": None, "error": str(exc)}

    threading.Thread(target=_run, daemon=True).start()
    return job_id


def get(job_id: str) -> dict[str, Any]:
    with _lock:
        return dict(_jobs.get(job_id, {"status": "unknown", "result": None, "error": None}))
