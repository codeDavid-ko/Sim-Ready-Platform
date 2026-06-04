#!/usr/bin/env bash
# 백엔드(8000) + 프런트(3000) 동시 실행.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
(cd "$ROOT/backend" && uv run python -m algo_runner._serve) &
(cd "$ROOT/frontend" && pnpm dev) &
echo "백엔드 http://localhost:8000  프런트 http://localhost:3000  (Ctrl+C 로 종료)"
wait
