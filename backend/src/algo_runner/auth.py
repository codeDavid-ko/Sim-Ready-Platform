"""아이디/비밀번호 + 역할(admin|user) 인증 — HMAC 서명 토큰(외부 의존성 0).

사용자는 users.py(파일 저장소)에 보관한다. 최초 실행 시(사용자 0명) /api/setup 으로
관리자가 직접 아이디/비밀번호를 정한다. 로그인하면 sub/role 이 담긴 토큰을 받고
Authorization: Bearer <token> 로 보호 엔드포인트에 접근한다. require_admin 은 추가로
현재 사용자 레코드의 role==admin 을 확인한다(권한 회수 즉시 반영).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from . import users
from .settings import Settings, get_settings

router = APIRouter(prefix="/api", tags=["auth"])
_TTL = 60 * 60 * 24 * 30  # 30일


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(secret: str, msg: str) -> str:
    return _b64e(hmac.new(secret.encode(), msg.encode(), hashlib.sha256).digest())


def issue_token(secret: str, username: str, role: str, ttl: int = _TTL) -> str:
    body = _b64e(json.dumps({"sub": username, "role": role, "exp": int(time.time()) + ttl}).encode())
    return f"{body}.{_sign(secret, body)}"


def decode_token(token: str, secret: str) -> dict | None:
    try:
        body, sig = token.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sign(secret, body)):
        return None
    try:
        payload = json.loads(_b64d(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload.get("exp"), int) or payload["exp"] < int(time.time()):
        return None
    return payload


class LoginIn(BaseModel):
    username: str
    password: str


class LoginOut(BaseModel):
    token: str
    username: str
    role: str


class StatusOut(BaseModel):
    auth_required: bool
    needs_setup: bool
    jobs_running: int = 0
    draining: bool = False


class MeOut(BaseModel):
    username: str
    role: str


@router.get("/status", response_model=StatusOut)
def status(s: Settings = Depends(get_settings)) -> StatusOut:
    from .workflows import jobs
    return StatusOut(auth_required=True, needs_setup=users.needs_setup(),
                     jobs_running=jobs.running_count(), draining=jobs.draining())


@router.post("/setup", response_model=LoginOut)
def setup(body: LoginIn, s: Settings = Depends(get_settings)) -> LoginOut:
    """최초 1회: 관리자 계정 생성(사용자가 0명일 때만). 생성 후 바로 로그인 토큰 발급."""
    try:
        who = users.create_first_admin(body.username, body.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    token = issue_token(s.resolve_auth_secret(), who["username"], who["role"])
    return LoginOut(token=token, username=who["username"], role=who["role"])


@router.post("/login", response_model=LoginOut)
def login(body: LoginIn, s: Settings = Depends(get_settings)) -> LoginOut:
    who = users.verify_credentials(body.username.strip(), body.password)
    if who is None:
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다.")
    token = issue_token(s.resolve_auth_secret(), who["username"], who["role"])
    return LoginOut(token=token, username=who["username"], role=who["role"])


def _current(request: Request, s: Settings) -> dict:
    """Bearer 토큰 검증 + 사용자 존재 확인 → {username, role(live)}."""
    header = request.headers.get("authorization", "")
    parts = header.split(None, 1)
    token = parts[1].strip() if len(parts) == 2 and parts[0].lower() == "bearer" else ""
    payload = decode_token(token, s.resolve_auth_secret()) if token else None
    if not payload:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    u = users.get_user(payload.get("sub", ""))
    if u is None:
        raise HTTPException(status_code=401, detail="존재하지 않는 사용자입니다.")
    return {"username": u["username"], "role": u.get("role", "user")}


def require_auth(request: Request, s: Settings = Depends(get_settings)) -> dict:
    """보호 엔드포인트 게이트. 유효 토큰 + 실존 사용자 필요."""
    return _current(request, s)


def require_admin(request: Request, s: Settings = Depends(get_settings)) -> dict:
    """관리자 전용 게이트 — 현재 사용자 레코드의 role==admin 확인."""
    who = _current(request, s)
    if who["role"] != "admin":
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    return who


@router.get("/me", response_model=MeOut)
def me(who: dict = Depends(require_auth)) -> MeOut:
    return MeOut(username=who["username"], role=who["role"])
