"""외부 공개(Cloudflare 터널) 켜기/끄기. 비밀번호 게이트 뒤."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .auth import require_auth
from .settings import Settings, get_settings
from .tunnel import TunnelManager, get_tunnel_manager

router = APIRouter(prefix="/api/admin", tags=["admin"])


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
    _gate: None = Depends(require_auth),
    s: Settings = Depends(get_settings),
    mgr: TunnelManager = Depends(get_tunnel_manager),
) -> TunnelOut:
    return TunnelOut.of(mgr.status(s.cloudflared_path))


@router.post("/tunnel/start", response_model=TunnelOut)
def tunnel_start(
    _gate: None = Depends(require_auth),
    s: Settings = Depends(get_settings),
    mgr: TunnelManager = Depends(get_tunnel_manager),
) -> TunnelOut:
    return TunnelOut.of(mgr.start(s.frontend_port, s.cloudflared_path))


@router.post("/tunnel/stop", response_model=TunnelOut)
def tunnel_stop(
    _gate: None = Depends(require_auth),
    s: Settings = Depends(get_settings),
    mgr: TunnelManager = Depends(get_tunnel_manager),
) -> TunnelOut:
    return TunnelOut.of(mgr.stop(s.cloudflared_path))
