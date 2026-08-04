"""파이프라인 스텝 어댑터 — 입력 USD → grasp_identifier_curve(파지축) 추가된 자기완결 USD.
params.points(월드 미터 2점) 가 있으면 그걸로, 없으면 AI(Claude)로 파지 2점을 추론."""
from __future__ import annotations

import asyncio
from pathlib import PurePath


def run(data: bytes, name: str, params: dict):
    from ...settings import get_settings
    from . import pipeline as gp

    pts = params.get("points")
    if not pts:
        s = get_settings()
        res = asyncio.run(gp.ai_suggest_grasp(
            data, name, context=params.get("context", "") or params.get("text", ""),
            api_key=s.anthropic_api_key, oauth_token=s.claude_code_oauth_token, model=s.claude_model))
        pts = res[0] if isinstance(res, (list, tuple)) else res
    # author_grasp 의 2번째 반환값은 '확장자'(.usd/.usdz)다 — 파일명이 아니다.
    # 그대로 넘기면 다음 스텝이 ".usdz" 를 파일명으로 받아 splitext 가 확장자를 못 떼고
    # usdz(zip)를 usda 로 열다 깨진다("is not a valid usda layer"). → 스템을 붙여 온전한 이름으로.
    out_bytes, out_ext, info = gp.author_grasp(data, name, pts)
    ext = out_ext if str(out_ext).startswith(".") else (PurePath(str(out_ext)).suffix or ".usd")
    out_name = f"{PurePath(name or 'asset').stem}_grasp{ext}"
    result = {"kind": "grasp"}
    if isinstance(info, dict):
        result.update({k: info[k] for k in ("target", "reason", "points_m", "grasp_path") if k in info})
    return out_bytes, out_name, "usd_physics", result
