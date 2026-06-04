"""content-physics 워크플로우 — NVIDIA content-agents Physics 파이프라인을
WSL2에서 오케스트레이션(공용 _ca_runner 사용).

Windows 백엔드 → wsl.exe → ~/content-agents/run_agent.sh physics
(Warp 멀티뷰 렌더 + 구독 Claude VLM via anthropic_oauth) → UsdPhysics(질량/마찰/
반발)가 적용된 USD 회수. content-material 과 동일 구조, 에이전트만 physics.
"""

from __future__ import annotations

from typing import Any

from .._ca_runner import run_agent_card


def run(
    params: dict[str, Any],
    file_bytes: bytes | None = None,
    file_name: str | None = None,
    ctx: Any = None,
) -> dict[str, Any]:
    res = run_agent_card("physics", file_bytes, file_name, ctx)
    res["engine"] = "NVIDIA content-agents (Physics) · Warp 멀티뷰 렌더 · 구독 Claude VLM"
    return res
