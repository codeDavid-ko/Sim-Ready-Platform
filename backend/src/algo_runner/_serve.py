"""실행 진입점 — Windows asyncio subprocess 가드(winloop).

Windows 기본 이벤트 루프는 subprocess 미지원이라 claude-agent-sdk(claude CLI spawn)가 깨진다.
winloop 로 띄워 해결. 비-Windows 는 기본 uvicorn.
"""

from __future__ import annotations

import os
import sys


def main() -> None:
    import uvicorn

    host = os.environ.get("ALGO_HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))

    if sys.platform == "win32":
        try:
            import winloop

            winloop.install()
            loop = "none"
        except Exception:  # noqa: BLE001
            loop = "asyncio"
    else:
        loop = "asyncio"

    print(f"[algo-runner] host={host} port={port} loop={loop} platform={sys.platform}")
    uvicorn.run("algo_runner.main:app", host=host, port=port, loop=loop)


if __name__ == "__main__":
    main()
