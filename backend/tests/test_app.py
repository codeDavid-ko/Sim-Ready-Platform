"""기본 동작 테스트 — 인증 게이트 + /api/run."""
from __future__ import annotations

import io

from fastapi.testclient import TestClient

from algo_runner.auth import issue_token
from algo_runner.main import create_app
from algo_runner.settings import get_settings


def _client() -> TestClient:
    return TestClient(create_app())


def test_health() -> None:
    assert _client().get("/api/health").json()["status"] == "ok"


def test_run_open_when_no_password(monkeypatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "app_password", "")  # 공개 모드
    c = _client()
    r = c.post("/api/run", data={"n": "10"})
    assert r.status_code == 200
    assert r.json()["result"]["sum_1_to_n"] == 55


def test_run_requires_token_when_password_set(monkeypatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "app_password", "secret")
    c = _client()
    # 토큰 없으면 401
    assert c.post("/api/run", data={"n": "3"}).status_code == 401
    # 틀린 비번 401
    assert c.post("/api/login", json={"password": "nope"}).status_code == 401
    # 맞는 비번 → 토큰 → 통과
    tok = c.post("/api/login", json={"password": "secret"}).json()["token"]
    r = c.post("/api/run", data={"n": "3"}, headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and r.json()["result"]["sum_1_to_n"] == 6


def test_run_with_file(monkeypatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "app_password", "")
    c = _client()
    r = c.post(
        "/api/run",
        data={"label": "x"},
        files={"file": ("a.txt", io.BytesIO(b"line1\nline2"), "text/plain")},
    )
    body = r.json()["result"]
    assert body["file"]["name"] == "a.txt" and body["line_count"] == 2
