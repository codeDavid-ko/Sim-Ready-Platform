"""파이프라인 스텝 어댑터 — 입력 USD → 관절(UsdPhysics articulation) 얹은 USD.
params.joints(child/parent/type/axis/pivot/limit) 가 있으면 그걸로, 없으면 AI(Claude)로 관절 추론.
관절이 0개면 강체/충돌만 얹어 통과(자동 추론 실패·정적 자산 대비)."""
from __future__ import annotations

import asyncio
from pathlib import PurePath


def _as_bool(v, default: bool) -> bool:
    """파이프라인 UI 의 select 는 값을 '문자열'로 준다 — bool("false") 는 True 라서 그대로 쓰면 안 된다."""
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on", "y")


def run(data: bytes, name: str, params: dict):
    from ...settings import get_settings
    from . import pipeline as ap

    joints = params.get("joints")
    if joints is None:
        s = get_settings()
        try:
            res = asyncio.run(ap.ai_suggest_joints(
                data, name, context=params.get("context", "") or params.get("text", ""),
                images=params.get("images") or None,   # 엔진이 (bytes,ctype) 튜플로 주입
                api_key=s.anthropic_api_key, oauth_token=s.claude_code_oauth_token, model=s.claude_model))
            joints = res[0] if isinstance(res, (list, tuple)) else res
        except Exception:  # noqa: BLE001 — 자격증명/추론 실패 시 관절 없이 강체만(정적 처리)
            joints = []
    out_bytes, authored, _notes, ext = ap.author_preserve(
        data, name, joints or [],
        drive=_as_bool(params.get("drive"), True), weld_loose=_as_bool(params.get("weld_loose"), True))
    out_name = f"{PurePath(name or 'asset').stem}_articulated{ext}"
    return out_bytes, out_name, "usd_physics", {"kind": "articulation", "joints": len(authored or [])}
