"""FastAPI 앱 진입점."""

from __future__ import annotations

import sys

if sys.platform == "win32":
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import api_admin, api_run, api_workflows, auth
from .settings import get_settings
from .workflows import registry


def create_app() -> FastAPI:
    app = FastAPI(title="algo-runner", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    app.include_router(auth.router)
    app.include_router(api_run.router)
    app.include_router(api_workflows.router)
    app.include_router(api_admin.router)

    # 다단계 워크플로우가 노출하는 자체 라우터를 /api/workflows/{id} 로 마운트
    for wf_id, wf_router in registry.routers():
        app.include_router(wf_router, prefix=f"/api/workflows/{wf_id}")

    return app


app = create_app()
