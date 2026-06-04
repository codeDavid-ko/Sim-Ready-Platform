"""content-texture 워크플로우 — NVIDIA content-agents Texture 파이프라인을
WSL2에서 오케스트레이션(공용 _ca_runner 사용).

run_agent.sh texture: Warp 멀티뷰 렌더 + 구독 Claude VLM 으로 재질/텍스처 추론.
텍스처 생성(image-gen NIM)은 NVIDIA_API_KEY 가 .env 에 있을 때만 동작한다.
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
    res = run_agent_card("texture", file_bytes, file_name, ctx)
    res["engine"] = "NVIDIA content-agents (Texture) · Warp 멀티뷰 렌더 · 구독 Claude VLM"
    return res
