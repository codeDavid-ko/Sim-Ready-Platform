"""변환 결과(에셋) 저장소 — 파일 + registry.json.

"저장소에 등록" 출력을 위한 최소 구현. ~/.algo-runner/assets/ 밑에
에셋별 폴더 + 전체 목록(registry.json)을 둔다. DB 없이 파일 기반.
"""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any

_STATE_DIR = Path.home() / ".algo-runner"
_ASSETS_DIR = _STATE_DIR / "assets"
_REGISTRY = _ASSETS_DIR / "registry.json"
_LOCK = threading.Lock()


def _read_registry() -> list[dict[str, Any]]:
    if _REGISTRY.exists():
        try:
            return json.loads(_REGISTRY.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return []
    return []


def _write_registry(items: list[dict[str, Any]]) -> None:
    _ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    _REGISTRY.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def register_asset(
    workflow_id: str,
    display_name: str,
    filename: str,
    data: bytes,
    meta: dict[str, Any],
) -> dict[str, Any]:
    """에셋 파일을 저장하고 레지스트리에 한 줄 추가. 등록 레코드를 돌려준다."""
    asset_id = uuid.uuid4().hex[:12]
    dest_dir = _ASSETS_DIR / asset_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / filename).write_bytes(data)
    record = {
        "id": asset_id,
        "workflow_id": workflow_id,
        "name": display_name,
        "filename": filename,
        "bytes": len(data),
        "meta": meta,
        "download_url": f"/api/workflows/{workflow_id}/assets/{asset_id}/download",
    }
    with _LOCK:
        items = _read_registry()
        items.append(record)
        _write_registry(items)
    return record


def list_assets() -> list[dict[str, Any]]:
    with _LOCK:
        return _read_registry()


def asset_path(asset_id: str) -> Path | None:
    for r in list_assets():
        if r["id"] == asset_id:
            p = _ASSETS_DIR / asset_id / r["filename"]
            return p if p.exists() else None
    return None
