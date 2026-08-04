"""아주 가벼운 인메모리 잡 스토어 — 긴 워크플로우(content-material, compare, 렌더 등)를
백그라운드 스레드로 돌리고 폴링으로 결과를 받는다. 취소도 지원한다.

긴 동기 요청이 (Next 개발 프록시 등에서) 타임아웃되는 문제를 피하기 위함.
프로세스 메모리에만 저장(재시작 시 사라짐) — 데모/단일 인스턴스용.

취소: cancel(job_id) → 잡의 cancel 이벤트를 세우고, 그 잡이 띄운 자식 프로세스(Isaac/WSL
등)를 프로세스 트리째 종료한다. 잡 함수는 jobs.run(...) 으로 서브프로세스를 돌리면
취소 시 즉시 죽고 JobCancelled 가 발생해 상태가 'cancelled' 가 된다.
"""

from __future__ import annotations

import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

_jobs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()
_current = threading.local()  # 실행 중 스레드의 job_id 보관

# 무중단 업데이트용 드레인 플래그(파일). graceful_reload 스크립트가 켜면 새 잡을 거부하고
# 진행 중 잡이 끝나길 기다린 뒤 백엔드를 재시작한다(돌던 잡을 안 죽임).
_DRAIN_FILE = Path.home() / ".algo-runner" / "DRAINING"


class JobCancelled(Exception):
    """사용자가 취소한 잡에서 발생 — submit 이 잡아 상태를 'cancelled' 로 만든다."""


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
        _jobs[job_id] = {
            "status": "running", "result": None, "error": None,
            "cancel": threading.Event(), "procs": [], "progress": None, "pipe": None,
        }

    def _run() -> None:
        _current.job_id = job_id
        try:
            result = fn()
            with _lock:
                # 결과 직전에 취소됐으면 취소로 기록
                if _jobs.get(job_id, {}).get("cancel") and _jobs[job_id]["cancel"].is_set():
                    _jobs[job_id] = {"status": "cancelled", "result": None, "error": "사용자 취소"}
                else:
                    _jobs[job_id] = {"status": "done", "result": result, "error": None}
        except JobCancelled:
            with _lock:
                _jobs[job_id] = {"status": "cancelled", "result": None, "error": "사용자 취소"}
        except Exception as exc:  # noqa: BLE001
            cancelled = bool(_jobs.get(job_id, {}).get("cancel") and _jobs[job_id]["cancel"].is_set())
            with _lock:
                if cancelled:
                    _jobs[job_id] = {"status": "cancelled", "result": None, "error": "사용자 취소"}
                else:
                    _jobs[job_id] = {"status": "error", "result": None, "error": str(exc)}
        finally:
            _current.job_id = None

    threading.Thread(target=_run, daemon=True).start()
    return job_id


def current_job() -> str | None:
    """현재 스레드에서 실행 중인 job_id(워처 스레드에 넘겨 진행률 보고용)."""
    return getattr(_current, "job_id", None)


def set_progress(job_id: str, progress: dict[str, Any]) -> None:
    """잡 진행률 갱신(폴링으로 노출). 예: {phase, frame, total}."""
    with _lock:
        j = _jobs.get(job_id)
        if j is not None and j.get("status") == "running":
            j["progress"] = progress


def set_pipeline_progress(job_id: str, pipe: dict[str, Any]) -> None:
    """파이프라인 스텝 진행률을 별도 슬롯에 저장 — 스텝 내부 카드가 progress 를 덮어써도
    파이프라인의 '스텝 N/총' 정보는 보존된다. 예: {index, total, label, id}."""
    with _lock:
        j = _jobs.get(job_id)
        if j is not None and j.get("status") == "running":
            j["pipe"] = pipe


def is_cancelled() -> bool:
    """현재 스레드의 잡이 취소 요청됐는지(잡 함수 안에서 폴링용)."""
    jid = getattr(_current, "job_id", None)
    if not jid:
        return False
    with _lock:
        ev = _jobs.get(jid, {}).get("cancel")
    return bool(ev and ev.is_set())


def _kill_tree(pid: int) -> None:
    """Windows 프로세스 트리 종료(자식까지). Isaac python.bat / wsl.exe 런처 등."""
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, timeout=20)
    except Exception:  # noqa: BLE001
        pass


def run(cmd: list[str], timeout: int | None = None, **kwargs: Any) -> subprocess.CompletedProcess:
    """취소 가능한 subprocess 실행. 현재 잡이 취소되면 프로세스 트리를 죽이고 JobCancelled.

    subprocess.run 대체용 — capture_output/text 등 kwargs 그대로 전달.
    """
    jid = getattr(_current, "job_id", None)
    # capture_output 은 subprocess.run 전용 — Popen 엔 stdout/stderr=PIPE 로 변환.
    if kwargs.pop("capture_output", True):
        kwargs.setdefault("stdout", subprocess.PIPE)
        kwargs.setdefault("stderr", subprocess.PIPE)
    kwargs.setdefault("text", True)
    proc = subprocess.Popen(cmd, **kwargs)
    if jid:
        with _lock:
            j = _jobs.get(jid)
            if j is not None:
                j["procs"].append(proc)
    t0 = time.time()
    try:
        while True:
            try:
                out, err = proc.communicate(timeout=1.0)
                return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
            except subprocess.TimeoutExpired:
                pass
            if is_cancelled():
                _kill_tree(proc.pid)
                try:
                    proc.wait(timeout=10)
                except Exception:  # noqa: BLE001
                    pass
                raise JobCancelled()
            if timeout is not None and (time.time() - t0) > timeout:
                _kill_tree(proc.pid)
                raise subprocess.TimeoutExpired(cmd, timeout)
    finally:
        if jid:
            with _lock:
                j = _jobs.get(jid)
                if j is not None and proc in j.get("procs", []):
                    j["procs"].remove(proc)


def register_proc(proc: Any) -> None:
    """직접 Popen 한 자식 프로세스를 현재 잡에 등록(취소 시 트리 kill 대상). jobs.run 을
    못 쓰는 비동기 오케스트레이션(예: GUI 앱을 띄워두고 따로 녹화)용."""
    jid = getattr(_current, "job_id", None)
    if jid:
        with _lock:
            j = _jobs.get(jid)
            if j is not None:
                j["procs"].append(proc)


def unregister_proc(proc: Any) -> None:
    jid = getattr(_current, "job_id", None)
    with _lock:
        j = _jobs.get(jid)
        if j is not None and proc in j.get("procs", []):
            j["procs"].remove(proc)


def kill_tree(pid: int) -> None:
    """프로세스 트리 종료(공개 래퍼)."""
    _kill_tree(pid)


def cancel(job_id: str) -> bool:
    """잡 취소: cancel 이벤트 세우고 등록된 자식 프로세스를 트리째 종료."""
    with _lock:
        j = _jobs.get(job_id)
        if not j or j.get("status") != "running":
            return False
        ev = j.get("cancel")
        procs = list(j.get("procs", []))
    if ev:
        ev.set()
    for p in procs:
        try:
            _kill_tree(p.pid)
        except Exception:  # noqa: BLE001
            pass
    return True


def get(job_id: str) -> dict[str, Any]:
    with _lock:
        j = _jobs.get(job_id, {"status": "unknown", "result": None, "error": None})
        return {"status": j.get("status"), "result": j.get("result"),
                "error": j.get("error"), "progress": j.get("progress"), "pipe": j.get("pipe")}
