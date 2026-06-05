"""파일 기반 사용자 저장소 — 아이디/비밀번호 + 역할(admin|user).

~/.algo-runner/users.json 에 저장. 비밀번호는 PBKDF2-HMAC-SHA256(per-user salt).
외부 의존성 0. 최초 1회 admin 을 부트스트랩한다(APP_PASSWORD 를 admin 초기 비번으로).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

_STATE_DIR = Path.home() / ".algo-runner"
_USERS = _STATE_DIR / "users.json"
_LOCK = threading.RLock()
_ITER = 200_000

ROLES = ("admin", "user")


# ───────────────────────── 비밀번호 해시 ─────────────────────────
def _pbkdf2(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITER).hex()


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    return f"{salt.hex()}:{_pbkdf2(password, salt)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, h = stored.split(":", 1)
        return hmac.compare_digest(_pbkdf2(password, bytes.fromhex(salt_hex)), h)
    except (ValueError, TypeError):
        return False


# ───────────────────────── 저장 ─────────────────────────
def _read() -> list[dict[str, Any]]:
    if _USERS.exists():
        try:
            data = json.loads(_USERS.read_text(encoding="utf-8"))
            return data.get("users", []) if isinstance(data, dict) else []
        except (ValueError, OSError):
            return []
    return []


def _write(users: list[dict[str, Any]]) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _USERS.write_text(json.dumps({"users": users}, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_bootstrap(admin_username: str, admin_password: str) -> None:
    """사용자가 하나도 없으면 admin 계정을 만든다(최초 1회)."""
    with _LOCK:
        users = _read()
        if users:
            return
        pw = admin_password or "changeme"
        _write([
            {
                "username": admin_username or "admin",
                "role": "admin",
                "pw": hash_password(pw),
                "created": int(time.time()),
            }
        ])


def list_users() -> list[dict[str, Any]]:
    """비밀번호 해시 제외한 공개 목록."""
    with _LOCK:
        return [
            {"username": u["username"], "role": u.get("role", "user"), "created": u.get("created", 0)}
            for u in _read()
        ]


def get_user(username: str) -> dict[str, Any] | None:
    with _LOCK:
        for u in _read():
            if u["username"] == username:
                return u
    return None


def verify_credentials(username: str, password: str) -> dict[str, Any] | None:
    u = get_user(username)
    if u and verify_password(password, u.get("pw", "")):
        return {"username": u["username"], "role": u.get("role", "user")}
    return None


def create_user(username: str, password: str, role: str = "user") -> dict[str, Any]:
    username = (username or "").strip()
    if not username:
        raise ValueError("아이디를 입력하세요.")
    if not password:
        raise ValueError("비밀번호를 입력하세요.")
    if role not in ROLES:
        raise ValueError(f"역할은 {ROLES} 중 하나여야 합니다.")
    with _LOCK:
        users = _read()
        if any(u["username"] == username for u in users):
            raise ValueError(f"이미 존재하는 아이디: {username}")
        rec = {"username": username, "role": role, "pw": hash_password(password), "created": int(time.time())}
        users.append(rec)
        _write(users)
    return {"username": username, "role": role, "created": rec["created"]}


def set_password(username: str, password: str) -> None:
    if not password:
        raise ValueError("비밀번호를 입력하세요.")
    with _LOCK:
        users = _read()
        for u in users:
            if u["username"] == username:
                u["pw"] = hash_password(password)
                _write(users)
                return
    raise ValueError(f"사용자를 찾을 수 없습니다: {username}")


def set_role(username: str, role: str) -> None:
    if role not in ROLES:
        raise ValueError(f"역할은 {ROLES} 중 하나여야 합니다.")
    with _LOCK:
        users = _read()
        target = next((u for u in users if u["username"] == username), None)
        if target is None:
            raise ValueError(f"사용자를 찾을 수 없습니다: {username}")
        # 마지막 admin 의 권한을 내리지 못하게 보호
        if target.get("role") == "admin" and role != "admin":
            if sum(1 for u in users if u.get("role") == "admin") <= 1:
                raise ValueError("마지막 관리자의 권한은 변경할 수 없습니다.")
        target["role"] = role
        _write(users)


def delete_user(username: str) -> None:
    with _LOCK:
        users = _read()
        target = next((u for u in users if u["username"] == username), None)
        if target is None:
            raise ValueError(f"사용자를 찾을 수 없습니다: {username}")
        if target.get("role") == "admin" and sum(1 for u in users if u.get("role") == "admin") <= 1:
            raise ValueError("마지막 관리자는 삭제할 수 없습니다.")
        _write([u for u in users if u["username"] != username])
