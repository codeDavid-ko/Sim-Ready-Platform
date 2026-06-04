"""선택: Claude 호출 헬퍼. ANTHROPIC_API_KEY 또는 CLAUDE_CODE_OAUTH_TOKEN 있을 때만 동작.

알고리즘 안에서 `from .llm import ask_claude` 로 쓰면 된다. Claude 가 필요 없으면 이 파일과
pyproject 의 claude-agent-sdk 의존성을 지워도 됨.
"""

from __future__ import annotations

import os

from .settings import get_settings


def _sync_env() -> None:
    s = get_settings()
    if s.anthropic_api_key and not os.environ.get("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = s.anthropic_api_key
    if s.claude_code_oauth_token and not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = s.claude_code_oauth_token


def has_claude() -> bool:
    s = get_settings()
    return bool(
        s.anthropic_api_key
        or s.claude_code_oauth_token
        or os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    )


async def ask_claude_async(prompt: str, system: str | None = None) -> str:
    if not has_claude():
        raise RuntimeError("Claude 자격증명이 없습니다(.env 에 ANTHROPIC_API_KEY 또는 CLAUDE_CODE_OAUTH_TOKEN).")
    _sync_env()
    from claude_agent_sdk import ClaudeAgentOptions, query  # type: ignore[import-not-found]

    kwargs: dict = {"model": get_settings().claude_model}
    if system:
        kwargs["system_prompt"] = system
    options = ClaudeAgentOptions(**kwargs)
    parts: list[str] = []
    async for message in query(prompt=prompt, options=options):
        for block in (getattr(message, "content", None) or []):
            t = getattr(block, "text", None)
            if t:
                parts.append(t)
    return "".join(parts).strip()


def ask_claude(prompt: str, system: str | None = None) -> str:
    """동기 래퍼(이미 이벤트 루프 안이면 ask_claude_async 를 await 하세요)."""
    import asyncio

    return asyncio.run(ask_claude_async(prompt, system))
