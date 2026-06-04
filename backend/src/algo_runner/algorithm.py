"""★★★ 여기에 당신의 알고리즘을 넣으세요 ★★★

run_algorithm() 안만 고치면 됩니다.
- params:     프런트 폼에서 온 값들(dict). 예: params.get("n")
- file_bytes: 업로드 파일 바이트(없으면 None)
- file_name:  업로드 파일 이름(없으면 None)
- 반환:        JSON 으로 직렬화 가능한 dict (프런트에 그대로 표시됨)

Claude 가 필요하면:
    from .llm import ask_claude
    answer = ask_claude("...")   # 키 없으면 RuntimeError → try/except 로 처리
"""

from __future__ import annotations

from typing import Any


def run_algorithm(
    params: dict[str, Any],
    file_bytes: bytes | None = None,
    file_name: str | None = None,
) -> dict[str, Any]:
    # ── 샘플 구현(지우고 당신 로직으로 교체) ──────────────────────────
    result: dict[str, Any] = {"received_params": params}

    # 파일이 오면 기본 정보
    if file_bytes is not None:
        result["file"] = {"name": file_name, "bytes": len(file_bytes)}
        # 텍스트 파일이면 줄 수 예시
        try:
            text = file_bytes.decode("utf-8")
            result["line_count"] = text.count("\n") + 1
        except UnicodeDecodeError:
            pass

    # 숫자 파라미터 예시: n 이 오면 1..n 합
    n = params.get("n")
    if n is not None:
        try:
            n_int = int(n)
            result["sum_1_to_n"] = n_int * (n_int + 1) // 2
        except (TypeError, ValueError):
            result["error"] = f"n 은 정수여야 합니다: {n!r}"

    return result
    # ─────────────────────────────────────────────────────────────
