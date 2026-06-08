"""아주 가벼운 인메모리 잡 스토어 — 긴 워크플로우(content-material, compare)를
백그라운드 스레드로 돌리고 폴링으로 결과를 받는다.

긴 동기 요청이 (Next 개발 프록시 등에서) 타임아웃되는 문제를 피하기 위함.
프로세스 메모리에만 저장(재시작 시 사라짐) — 데모/단일 인스턴스용.
"""

from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any, Callable

_jobs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()

# 무중단 업데이트용 드레인 플래그(파일). graceful_reload 스크립트가 켜면 새 잡을 거부하고
# 진행 중 잡이 끝나길 기다린 뒤 백엔드를 재시작한다(돌던 잡을 안 죽임).
_DRAIN_FILE = Path.home() / ".algo-runner" / "DRAINING"


def draining() -> bool:
    return _DRAIN_FILE.exists()


def running_count() -> int:
    with _lock:
        return sum(1 for j in _jobs.values() if j.get("status") == "running")


def submit(fn: Callable[[], Any]) -> str:
    """fn 을 데몬 스레드로 실행하고 job_id 반환."""
    if _DRAIN_FILE.exists():
        raise RuntimeError("서버 업데이트 준비 중입니다. 잠시 후 다시 시도하세요.")
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
