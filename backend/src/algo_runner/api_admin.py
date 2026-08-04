"""관리자 API — 외부 공개(Cloudflare 터널) + 사용자(아이디/비밀번호) 관리.

모두 관리자 권한(require_admin) 뒤에 있다."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import users
from .auth import require_admin
from .settings import Settings, get_settings
from .tunnel import TunnelManager, get_tunnel_manager

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ───────────────────────── 사용자 관리 ─────────────────────────
class UserIn(BaseModel):
    username: str
    password: str
    role: str = "user"
    cards: list[str] = []   # 허용 카드(표시용 권한)


class PasswordIn(BaseModel):
    password: str


class RoleIn(BaseModel):
    role: str


class CardsIn(BaseModel):
    cards: list[str] = []


@router.get("/users")
def list_users(who: dict = Depends(require_admin)) -> dict[str, Any]:
    return {"users": users.list_users(), "me": who["username"]}


@router.post("/users")
def create_user(body: UserIn, _who: dict = Depends(require_admin)) -> dict[str, Any]:
    try:
        rec = users.create_user(body.username, body.password, body.role, body.cards)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"ok": True, "user": rec}


@router.post("/users/{username}/cards")
def change_cards(username: str, body: CardsIn, _who: dict = Depends(require_admin)) -> dict[str, Any]:
    try:
        users.set_cards(username, body.cards)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"ok": True}


@router.post("/users/{username}/password")
def reset_password(username: str, body: PasswordIn, _who: dict = Depends(require_admin)) -> dict[str, Any]:
    try:
        users.set_password(username, body.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"ok": True}


@router.post("/users/{username}/role")
def change_role(username: str, body: RoleIn, _who: dict = Depends(require_admin)) -> dict[str, Any]:
    try:
        users.set_role(username, body.role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"ok": True}


@router.delete("/users/{username}")
def delete_user(username: str, who: dict = Depends(require_admin)) -> dict[str, Any]:
    if username == who["username"]:
        raise HTTPException(status_code=400, detail="자기 자신은 삭제할 수 없습니다.")
    try:
        users.delete_user(username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"ok": True}


class TunnelOut(BaseModel):
    running: bool
    url: str | None
    error: str | None
    installed: bool

    @classmethod
    def of(cls, st) -> "TunnelOut":
        return cls(running=st.running, url=st.url, error=st.error, installed=st.installed)


@router.get("/tunnel", response_model=TunnelOut)
def tunnel_status(
    _gate: dict = Depends(require_admin),
    s: Settings = Depends(get_settings),
    mgr: TunnelManager = Depends(get_tunnel_manager),
) -> TunnelOut:
    return TunnelOut.of(mgr.status(s.cloudflared_path))


@router.post("/tunnel/start", response_model=TunnelOut)
def tunnel_start(
    _gate: dict = Depends(require_admin),
    s: Settings = Depends(get_settings),
    mgr: TunnelManager = Depends(get_tunnel_manager),
) -> TunnelOut:
    return TunnelOut.of(mgr.start(s.frontend_port, s.cloudflared_path))


@router.post("/tunnel/stop", response_model=TunnelOut)
def tunnel_stop(
    _gate: dict = Depends(require_admin),
    s: Settings = Depends(get_settings),
    mgr: TunnelManager = Depends(get_tunnel_manager),
) -> TunnelOut:
    return TunnelOut.of(mgr.stop(s.cloudflared_path))
