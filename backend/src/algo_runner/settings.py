"""런타임 설정 (env > .env > 기본값)."""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_STATE_DIR = Path.home() / ".algo-runner"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_password: str = ""        # admin 초기 비밀번호(부트스트랩용). 이후엔 users.json 이 출처.
    admin_username: str = "admin" # 부트스트랩되는 최초 관리자 아이디
    auth_secret: str = ""         # 비우면 자동 생성·재사용
    allowed_origins: str = ""     # 외부공개 URL(쉼표). 비우면 localhost:3000
    port: int = 8000
    frontend_port: int = 3000
    cloudflared_path: str = ""

    # Claude (둘 중 하나 있으면 ask_claude 동작)
    anthropic_api_key: str = ""
    claude_code_oauth_token: str = ""
    claude_model: str = "claude-opus-4-8"

    # material-usd 워크플로우: NVIDIA vMaterials 설치 경로(빌드 시 MDL 참조). 비우면 뷰어에서 핑크/검정.
    vmaterials_root: str = ""

    # 이미지 생성(텍스처) 백엔드 키 — content-texture 의 generate_textures 가 필요로 함.
    nvidia_api_key: str = ""
    openai_api_key: str = ""
    google_api_key: str = ""

    def image_gen_available(self) -> bool:
        """텍스처 '생성'(diffusion)이 가능한 외부 키가 하나라도 있는지."""
        return bool(self.nvidia_api_key or self.openai_api_key or self.google_api_key)

    # Trinix CAD MCP (이미지/텍스트 → 3D 모델). 토큰 + 라이브 페어링 세션 필요.
    trinix_ai_token: str = ""
    trinix_mcp_endpoint: str = "https://mcp.trinix-ai.com/mcp"
    trinix_platform_dir: str = r"C:\Users\user\Desktop\Claude\GIT\trinix-modeling-platform"
    # keep_session 이 export 다운로드를 떨구는 폴더(백엔드가 여기서 결과 파일 회수).
    trinix_drop_dir: str = ""

    def cors_origins(self) -> list[str]:
        items = [o.strip() for o in self.allowed_origins.split(",") if o.strip()]
        return items or ["http://localhost:3000"]

    def resolve_auth_secret(self) -> str:
        if self.auth_secret:
            return self.auth_secret
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        f = _STATE_DIR / "auth_secret"
        if f.exists():
            v = f.read_text(encoding="utf-8").strip()
            if v:
                return v
        v = secrets.token_urlsafe(48)
        f.write_text(v, encoding="utf-8")
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
