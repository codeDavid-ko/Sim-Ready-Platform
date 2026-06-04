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
    return app


app = create_app()
