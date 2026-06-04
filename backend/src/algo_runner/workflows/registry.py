"""매니페스트 기반 워크플로우 레지스트리 (규약 v1).

이 패키지 밑의 각 하위 폴더에서 `manifest.json` 을 찾아 자동 등록한다.
워크플로우는 다음 중 하나(또는 둘 다)를 가질 수 있다:
  - `handler.py` 의 `run(params, file_bytes, file_name, ctx) -> dict`  (원샷 워크플로우)
  - `routes.py` 의 `router`(APIRouter)                                 (다단계/자체 엔드포인트)

셸은 매니페스트만 읽어 카드를 그린다. 내부 구현(원샷이냐 다단계냐)은 모른다.
"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import storage

_WF_DIR = Path(__file__).parent

HandlerFn = Callable[..., dict[str, Any]]


@dataclass
class WorkflowContext:
    """핸들러에 주입되는 셸 측 핸들. 결과 에셋 등록 등 셸 자원 접근을 제공."""

    workflow_id: str

    def register_asset(
        self, display_name: str, filename: str, data: bytes, meta: dict[str, Any]
    ) -> dict[str, Any]:
        return storage.register_asset(self.workflow_id, display_name, filename, data, meta)


@dataclass
class Workflow:
    manifest: dict[str, Any]
    handler: HandlerFn | None = None  # 원샷 워크플로우
    router: Any = None               # 다단계 워크플로우(APIRouter)

    @property
    def id(self) -> str:
        return str(self.manifest["id"])


def _discover() -> dict[str, Workflow]:
    found: dict[str, Workflow] = {}
    for child in sorted(_WF_DIR.iterdir()):
        manifest_file = child / "manifest.json"
        if not child.is_dir() or not manifest_file.exists():
            continue
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))

        handler = None
        if (child / "handler.py").exists():
            module = importlib.import_module(f"{__package__}.{child.name}.handler")
            handler = getattr(module, "run", None)

        router = None
        if (child / "routes.py").exists():
            rmod = importlib.import_module(f"{__package__}.{child.name}.routes")
            router = getattr(rmod, "router", None)

        found[str(manifest["id"])] = Workflow(manifest=manifest, handler=handler, router=router)
    return found


_CACHE: dict[str, Workflow] | None = None


def all_workflows() -> dict[str, Workflow]:
    global _CACHE
    if _CACHE is None:
        _CACHE = _discover()
    return _CACHE


def list_manifests() -> list[dict[str, Any]]:
    return [wf.manifest for wf in all_workflows().values()]


def get(workflow_id: str) -> Workflow | None:
    return all_workflows().get(workflow_id)


def routers() -> list[tuple[str, Any]]:
    """(workflow_id, APIRouter) 목록 — main 이 /api/workflows/{id} 프리픽스로 마운트."""
    return [(wf.id, wf.router) for wf in all_workflows().values() if wf.router is not None]
