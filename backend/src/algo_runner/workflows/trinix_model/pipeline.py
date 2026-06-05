"""trinix-model 파이프라인 — 이미지/텍스트 → Trinix CAD 3D 모델 → STL export.

검증된 trinix-slack-bot/trinix_build.py 패턴을 Sim-Ready 카드로 이식(STEP→STL).
- 구동: claude-agent-sdk(구독 OAuth 토큰; [[claude-oauth-subscription-auth]]). Trinix MCP(http)를
  서버로 등록 → 구독 Claude 가 도구를 호출해 모델을 빌드.
- 시스템 프롬프트: trinix-modeling-platform/MODELING_AGENT_PROMPT.md
- export: Trinix 의 export_scene 으로 **STL** 을 절대경로에 저장(=“코드로 STL 뽑기”).
  Trinix 는 USD/URDF export 미지원이라 STL/STEP 만 가능 → 여기선 STL.

전제(둘 다 필요): TRINIX_AI_TOKEN(.env) + **라이브 페어링된 Trinix 에디터 세션**
(keep_session.py). 미충족이면 export 파일이 안 생겨 RuntimeError 로 명확히 보고한다.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parents[4]
_RUNS = _BACKEND / "_trinix_runs"

_IMG_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/webp": ".webp"}


def _prompt_path() -> Path:
    from ...settings import get_settings
    return Path(get_settings().trinix_platform_dir) / "MODELING_AGENT_PROMPT.md"


def trinix_available() -> bool:
    """토큰 + 시스템 프롬프트 파일이 있는지(페어링 세션은 런타임에만 확인 가능)."""
    from ...settings import get_settings
    s = get_settings()
    return bool(s.trinix_ai_token) and _prompt_path().exists()


def _task_prompt(image_paths: list[str], user_text: str, out_stl: Path) -> str:
    imgs = "\n".join(f"  - {p}" for p in image_paths) or "  (이미지 없음 — 텍스트만으로 추정 금지, 부족하면 보고)"
    text = user_text.strip() or "(텍스트 설명 없음)"
    return f"""다음 입력으로 Trinix CAD 에 3D 모델을 빌드하라. 시스템 프롬프트의 절차를 그대로 따른다.

[텍스트 설명/요구/치수]
{text}

[입력 이미지] — 먼저 Read 도구로 한 장씩 열어 끝까지 정독한 뒤 추상화 시트를 작성하라.
{imgs}

[필수 마무리]
1) 빌드가 끝나면 set_view 6면 + fit_all + take_screenshot 로 검증하고 bbox 를 보고한다.
2) 반드시 export_scene 으로 **STL** 파일을 다음 절대경로에 저장한다(휘발성 — 빌드 직후 즉시):
     {out_stl}
   (format 은 stl. Trinix 는 USD/URDF export 미지원.)
3) export 후 list_shapes 로 형상 수·부품 이름을 한 번 더 확인하고, 부품 목록을 요약해 보고하고 종료한다.

연결이 불안정해 ok:true 여도 반영 안 될 수 있다(유령 데이터). 쓰기 후엔 list_shapes/스크린샷으로 재확인하라.
"""


async def _build_async(images: list[tuple[bytes, str]], text: str, out_stl: Path, model: str) -> str:
    from claude_agent_sdk import ClaudeAgentOptions, query

    from ...llm import _sync_env
    from ...settings import get_settings

    s = get_settings()
    _sync_env()  # CLAUDE_CODE_OAUTH_TOKEN → os.environ (SDK 가 읽음)

    tmp_imgs: list[str] = []
    try:
        for data, mime in images:
            fd, p = tempfile.mkstemp(suffix=_IMG_EXT.get(mime, ".jpg"))
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            tmp_imgs.append(p)

        mcp_servers = {
            "trinix": {
                "type": "http",
                "url": s.trinix_mcp_endpoint,
                "headers": {"Authorization": f"Bearer {s.trinix_ai_token}"},
            }
        }
        opts = ClaudeAgentOptions(
            system_prompt=_prompt_path().read_text(encoding="utf-8"),
            mcp_servers=mcp_servers,
            permission_mode="bypassPermissions",
            max_turns=80,
            allowed_tools=["Read", "mcp__trinix"],
            model=model,
        )
        out: list[str] = []
        async for message in query(prompt=_task_prompt(tmp_imgs, text, out_stl), options=opts):
            for block in getattr(message, "content", None) or []:
                t = getattr(block, "text", None)
                if t:
                    out.append(t)
        return "".join(out).strip()
    finally:
        for p in tmp_imgs:
            try:
                os.remove(p)
            except OSError:
                pass


def build_stl(images: list[tuple[bytes, str]], text: str, model: str = "claude-opus-4-8") -> dict[str, Any]:
    """모델 빌드 → STL 회수. {stl_bytes, report, run_dir}. 실패 시 RuntimeError."""
    from ...settings import get_settings
    s = get_settings()
    if not s.trinix_ai_token:
        raise RuntimeError("TRINIX_AI_TOKEN 이 없습니다(.env 에 추가하세요).")
    if not _prompt_path().exists():
        raise RuntimeError(f"MODELING_AGENT_PROMPT.md 를 찾을 수 없습니다: {_prompt_path()}")

    rid = uuid.uuid4().hex[:12]
    rundir = _RUNS / rid
    rundir.mkdir(parents=True, exist_ok=True)
    out_stl = rundir / "model.stl"
    if out_stl.exists():
        out_stl.unlink()

    report = asyncio.run(_build_async(images, text, out_stl, model))
    if not out_stl.exists():
        raise RuntimeError(
            "STL export 파일이 생성되지 않았습니다. Trinix 에디터 세션 페어링(녹색)·프로젝트 "
            f"토큰을 확인하세요.\n에이전트 보고(끝부분):\n{report[-1200:]}"
        )
    return {"stl_bytes": out_stl.read_bytes(), "report": report, "run_dir": str(rundir)}


def preview_glb(stl_bytes: bytes) -> bytes:
    """STL → 뷰어용 GLB(형상만)."""
    import io

    import trimesh

    mesh = trimesh.load(io.BytesIO(stl_bytes), file_type="stl")
    if isinstance(mesh, trimesh.Scene):
        mesh = mesh.dump(concatenate=True)
    return mesh.export(file_type="glb")
