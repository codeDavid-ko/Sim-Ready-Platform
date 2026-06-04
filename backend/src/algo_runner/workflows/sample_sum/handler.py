"""샘플 워크플로우 — 스타터의 기존 run_algorithm() 을 그대로 워크플로우로 노출.

algorithm.py 는 건드리지 않는다. 레지스트리에 워크플로우가 여러 개 등록될 수 있음을
보이는 데모 겸, 기존 /api/run 동작과의 동치를 유지한다.
"""

from __future__ import annotations

from typing import Any

from ...algorithm import run_algorithm


def run(
    params: dict[str, Any],
    file_bytes: bytes | None = None,
    file_name: str | None = None,
    ctx: Any = None,
) -> dict[str, Any]:
    return run_algorithm(params, file_bytes, file_name)
