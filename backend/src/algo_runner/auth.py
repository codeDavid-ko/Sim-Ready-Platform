"""비밀번호 1개 게이트 — HMAC 서명 토큰(외부 의존성 0).

APP_PASSWORD 가 비어 있으면 인증 없음(공개). 설정돼 있으면 /api/login 으로 토큰을 받고
Authorization: Bearer <token> 로 보호된 엔드포인트에 접근.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .settings import Settings, get_settings

router = APIRouter(prefix="/api", tags=["auth"])
_TTL = 60 * 60 * 24 * 30  # 30일


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(secret: str, msg: str) -> str:
    return _b64e(hmac.new(secret.encode(), msg.encode(), hashlib.sha256).digest())


def issue_token(secret: str, ttl: int = _TTL) -> str:
    body = _b64e(json.dumps({"exp": int(time.time()) + ttl}).encode())
    return f"{body}.{_sign(secret, body)}"


def verify_token(token: str, secret: str) -> bool:
    try:
        body, sig = token.split(".", 1)
    except ValueError:
        return False
    if not hmac.compare_digest(sig, _sign(secret, body)):
        return False
    try:
        exp = json.loads(_b64d(body)).get("exp", 0)
    except (ValueError, json.JSONDecodeError):
        return False
    return isinstance(exp, int) and exp >= int(time.time())


class LoginIn(BaseModel):
    password: str


class LoginOut(BaseModel):
    token: str


class StatusOut(BaseModel):
    auth_required: bool


@router.get("/status", response_model=StatusOut)
def status(s: Settings = Depends(get_settings)) -> StatusOut:
    return StatusOut(auth_required=bool(s.app_password))


@router.post("/login", response_model=LoginOut)
def login(body: LoginIn, s: Settings = Depends(get_settings)) -> LoginOut:
    if not s.app_password:
        return LoginOut(token=issue_token(s.resolve_auth_secret()))  # 공개 모드
    if not hmac.compare_digest(body.password, s.app_password):
        raise HTTPException(status_code=401, detail="비밀번호가 올바르지 않습니다.")
    return LoginOut(token=issue_token(s.resolve_auth_secret()))


def require_auth(request: Request, s: Settings = Depends(get_settings)) -> None:
    """보호 엔드포인트 게이트. APP_PASSWORD 없으면 통과(공개)."""
    if not s.app_password:
        return
    header = request.headers.get("authorization", "")
    parts = header.split(None, 1)
    token = parts[1].strip() if len(parts) == 2 and parts[0].lower() == "bearer" else ""
    if not token or not verify_token(token, s.resolve_auth_secret()):
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
