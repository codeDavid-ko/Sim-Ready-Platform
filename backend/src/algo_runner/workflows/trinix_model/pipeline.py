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


def _to_posix(p: Path) -> str:
    """C:\\X\\Y -> /mnt/c/X/Y (Trinix export 는 POSIX 절대경로를 요구; 이 경로는 우리 C: 드라이브)."""
    s = str(p)
    if len(s) > 1 and s[1] == ":":
        return f"/mnt/{s[0].lower()}{s[2:].replace(chr(92), '/')}"
    return s.replace(chr(92), "/")


def _task_prompt(image_paths: list[str], user_text: str, out_posix: str) -> str:
    imgs = "\n".join(f"  - {p}" for p in image_paths) or "  (이미지 없음 — 텍스트만으로 추정 금지, 부족하면 보고)"
    text = user_text.strip() or "(텍스트 설명 없음)"
    return f"""다음 입력으로 Trinix CAD 에 3D 모델을 빌드하라. 시스템 프롬프트의 절차를 그대로 따른다.

[텍스트 설명/요구/치수]
{text}

[입력 이미지] — 먼저 Read 도구로 한 장씩 열어 끝까지 정독한 뒤 추상화 시트를 작성하라.
{imgs}

[중요] 다운스트림에서 **부품별로 재질을 입힐 것**이므로, 의미 있는 부품 단위로 형상을 나누고
각 shape 에 명확한 이름(resultUid)을 붙여라(예: wall_n, door_frame, bus_bar_1). 한 덩어리로 합치지 마라.

[필수 마무리]
1) 빌드가 끝나면 set_view 6면 + fit_all + take_screenshot 로 검증하고 bbox 를 보고한다.
2) **export_scene 은 호출하지 마라.** 파일 내보내기는 서버가 별도로 처리한다. 너는 형상만 완성하고,
   마지막에 list_shapes 로 최종 형상 수·부품 이름(UID)을 확인해 그 목록을 요약 보고하고 종료한다.

연결이 불안정해 ok:true 여도 반영 안 될 수 있다(유령 데이터). 쓰기 후엔 list_shapes/스크린샷으로 재확인하라.
"""


async def _build_async(images: list[tuple[bytes, str]], text: str, out_step: Path, model: str) -> str:
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
        async for message in query(prompt=_task_prompt(tmp_imgs, text, _to_posix(out_step)), options=opts):
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


def _drop_dir() -> Path:
    from ...settings import get_settings
    s = get_settings()
    return Path(s.trinix_drop_dir) if s.trinix_drop_dir else (_BACKEND / "_trinix_drop")


def _newest_export_since(start: float) -> Path | None:
    """keep_session 이 drop 폴더에 캡처한 export(다운로드) 중 start 이후 최신 STEP/STL."""
    d = _drop_dir()
    if not d.is_dir():
        return None
    cands = [
        p for ext in ("*.step", "*.stp", "*.stl")
        for p in d.glob(ext)
        if p.is_file() and p.stat().st_mtime >= start - 2
    ]
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


async def _export_via_mcp(out_posix: str, fmt: str, tries: int = 4) -> dict[str, Any]:
    """모델링과 별개로 raw MCP 클라이언트로 export_scene 직접 호출(같은 프로젝트=빌드된 씬).
    claude-agent-sdk 의 'unexpected payload type' 회피 + 경로/재시도 제어."""
    import json as _json

    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    from ...settings import get_settings
    s = get_settings()
    headers = {"Authorization": f"Bearer {s.trinix_ai_token}"}
    last = ""
    async with streamablehttp_client(s.trinix_mcp_endpoint, headers=headers) as (r, w, _):
        async with ClientSession(r, w) as mcp:
            await mcp.initialize()
            for _ in range(tries):
                try:
                    out = await mcp.call_tool("export_scene", {"path": out_posix, "format": fmt})
                    last = "".join(getattr(c, "text", "") for c in (out.content or []))
                    s2 = last[last.find("{"): last.rfind("}") + 1]
                    try:
                        if _json.loads(s2).get("ok"):
                            return {"ok": True, "raw": last}
                    except ValueError:
                        if '"ok":true' in last.replace(" ", "").lower():
                            return {"ok": True, "raw": last}
                except Exception as e:  # noqa: BLE001
                    last = f"{type(e).__name__}: {e}"
                await asyncio.sleep(2.5)
    return {"ok": False, "raw": last}


def build_step(images: list[tuple[bytes, str]], text: str, model: str = "claude-opus-4-8") -> dict[str, Any]:
    """모델 빌드(SDK) → export(raw MCP) → STEP 회수. {step_bytes, report, run_dir, src}.

    export_scene 은 POSIX 절대경로(= 우리 C: 의 /mnt/c 뷰)에 파일을 쓴다. 그 경로(=Windows 경로)
    에서 읽고, 안 되면 keep_session drop 폴더(다운로드 캡처)에서 폴백 회수."""
    import time

    from ...settings import get_settings
    s = get_settings()
    if not s.trinix_ai_token:
        raise RuntimeError("TRINIX_AI_TOKEN 이 없습니다(.env 에 추가하세요).")
    if not _prompt_path().exists():
        raise RuntimeError(f"MODELING_AGENT_PROMPT.md 를 찾을 수 없습니다: {_prompt_path()}")

    rid = uuid.uuid4().hex[:12]
    rundir = _RUNS / rid
    rundir.mkdir(parents=True, exist_ok=True)
    out_step = rundir / "model.step"
    if out_step.exists():
        out_step.unlink()

    start = time.time()
    report = asyncio.run(_build_async(images, text, out_step, model))  # 모델링만
    exp = asyncio.run(_export_via_mcp(_to_posix(out_step), "step"))     # 별도 export

    found: Path | None = None
    if out_step.exists():
        found = out_step
    else:
        alt = next((p for p in rundir.glob("model.st*p")), None)
        found = alt or _newest_export_since(start)  # drop 폴더(다운로드 캡처) 폴백
    if found is None:
        raise RuntimeError(
            "export 파일을 회수하지 못했습니다(Trinix export_scene 실패 또는 페어링 불안정).\n"
            f"export 응답: {exp.get('raw', '')[:400]}\n에이전트 보고(끝부분):\n{report[-700:]}"
        )
    return {"step_bytes": found.read_bytes(), "report": report, "run_dir": str(rundir),
            "src": str(found), "export_raw": exp.get("raw", "")[:300]}


def _load_step_scene(step_bytes: bytes):
    """STEP 바이트 → trimesh Scene(파트별 지오메트리, cascadio 경유)."""
    import io

    import trimesh

    return trimesh.load(io.BytesIO(step_bytes), file_type="step")


async def _shoot(mcp, width: int = 640, height: int = 512) -> list[tuple[str, bytes]]:
    """현재 Trinix 씬을 여러 각도로 캡처 → [(view, png_bytes)]. 인라인 이미지 블록 회수."""
    import base64

    def imgs(out, label):
        got = []
        for c in (out.content or []):
            data = getattr(c, "data", None)
            if getattr(c, "type", None) == "image" and data:
                try:
                    got.append((label, base64.b64decode(data)))
                except Exception:  # noqa: BLE001
                    pass
        return got

    shots: list[tuple[str, bytes]] = []
    try:
        # verify_views: 한 번 호출로 front/right/top 정사영 → 라운드트립 최소화
        shots += imgs(await asyncio.wait_for(mcp.call_tool("verify_views", {}), 60), "ortho")
    except Exception:  # noqa: BLE001
        pass
    if not shots:
        # 폴백: 정면 1장만
        try:
            await asyncio.wait_for(mcp.call_tool("fit_all", {}), 20)
            shots += imgs(await asyncio.wait_for(mcp.call_tool("take_screenshot", {"width": width, "height": height}), 30), "view")
        except Exception:  # noqa: BLE001
            pass
    return shots


async def _build_and_shoot_async(images: list[tuple[bytes, str]], text: str, model: str) -> tuple[str, list[tuple[str, bytes]]]:
    """모델 빌드(SDK) → 같은 프로젝트에 raw MCP 로 다각도 스크린샷 캡처."""
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    from ...settings import get_settings

    report = await _build_async(images, text, _RUNS / "_noexport.bin", model)  # 빌드만(프롬프트가 export 안 함)
    s = get_settings()
    headers = {"Authorization": f"Bearer {s.trinix_ai_token}"}
    async with streamablehttp_client(s.trinix_mcp_endpoint, headers=headers) as (r, w, _):
        async with ClientSession(r, w) as mcp:
            await mcp.initialize()
            shots = await _shoot(mcp)
    return report, shots


def _safe(s: str) -> str:
    r = "".join(c if c.isalnum() else "_" for c in str(s)).strip("_") or "part"
    return ("n_" + r) if r[0].isdigit() else r


def _bbox_proxy(shapes: list[dict]) -> tuple[bytes, bytes, list[dict]]:
    """list_shapes 의 부품별 bbox(mm, 월드) → 부품마다 바운딩박스 USD(crate)+STL 프록시.
    정밀 형상이 아니라 블록 근사. export_scene 고장 우회용(부품수·위치·크기는 정확)."""
    import numpy as np
    import trimesh
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdPhysics.SetStageKilogramsPerUnit(stage, 1.0)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    scene_tm = trimesh.Scene()
    table, seen = [], {}
    for sh in shapes:
        bb = sh.get("bbox") or {}
        mn, mx = bb.get("min"), bb.get("max")
        if not mn or not mx or len(mn) < 3 or len(mx) < 3:
            continue
        mn = [float(c) / 1000.0 for c in mn[:3]]
        mx = [float(c) / 1000.0 for c in mx[:3]]
        ext = [mx[i] - mn[i] for i in range(3)]
        ctr = [(mn[i] + mx[i]) / 2 for i in range(3)]
        uid = str(sh.get("uid") or sh.get("type") or "part")
        safe = _safe(uid); seen[safe] = seen.get(safe, 0) + 1
        nm = safe if seen[safe] == 1 else f"{safe}_{seen[safe]}"
        # USD: bbox 박스 mesh + RigidBody/Collision
        bx = UsdGeom.Xform.Define(stage, f"/World/{nm}")
        UsdPhysics.RigidBodyAPI.Apply(bx.GetPrim())
        v = [(mn[0],mn[1],mn[2]),(mx[0],mn[1],mn[2]),(mx[0],mx[1],mn[2]),(mn[0],mx[1],mn[2]),
             (mn[0],mn[1],mx[2]),(mx[0],mn[1],mx[2]),(mx[0],mx[1],mx[2]),(mn[0],mx[1],mx[2])]
        fidx = [0,1,2,0,2,3,4,6,5,4,7,6,0,4,5,0,5,1,1,5,6,1,6,2,2,6,7,2,7,3,3,7,4,3,4,0]
        g = UsdGeom.Mesh.Define(stage, f"/World/{nm}/geom")
        g.CreatePointsAttr([Gf.Vec3f(*p) for p in v])
        g.CreateFaceVertexCountsAttr([3]*12); g.CreateFaceVertexIndicesAttr(fidx)
        g.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
        UsdPhysics.CollisionAPI.Apply(g.GetPrim())
        UsdPhysics.MeshCollisionAPI.Apply(g.GetPrim()).CreateApproximationAttr(UsdPhysics.Tokens.boundingCube)
        # STL: trimesh box
        bxm = trimesh.creation.box(extents=ext); bxm.apply_translation(ctr)
        scene_tm.add_geometry(bxm, node_name=nm)
        table.append({"name": uid, "size_mm": [round(e*1000,1) for e in ext], "center_m": [round(c,4) for c in ctr]})
    fd, outp = tempfile.mkstemp(suffix=".usd"); os.close(fd)
    try:
        stage.Export(outp)
        with open(outp, "rb") as f:
            usd = f.read()
    finally:
        try: os.remove(outp)
        except OSError: pass
    stl = scene_tm.export(file_type="stl") if table else b""
    return usd, stl, table


async def _build_capture_async(images: list[tuple[bytes, str]], text: str, model: str):
    import json as _json

    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    from ...settings import get_settings

    # 빌드(에이전트)에 타임아웃 — Trinix 호출이 멈춰도 잡이 영원히 안 끝나는 것 방지.
    try:
        report = await asyncio.wait_for(_build_async(images, text, _RUNS / "_nx.bin", model), 420)
    except asyncio.TimeoutError:
        report = "(모델링 시간 초과 ~7분 — 현재까지 형상으로 캡처 시도. 에디터 MCP 연결(녹색) 확인 권장.)"
    except Exception as e:  # noqa: BLE001
        report = f"(빌드 오류: {type(e).__name__}: {str(e)[:200]})"
    s = get_settings()
    headers = {"Authorization": f"Bearer {s.trinix_ai_token}"}
    shapes, shots = [], []
    try:
        async with streamablehttp_client(s.trinix_mcp_endpoint, headers=headers) as (r, w, _):
            async with ClientSession(r, w) as mcp:
                await asyncio.wait_for(mcp.initialize(), 25)
                try:
                    t = "".join(getattr(c, "text", "") for c in (await asyncio.wait_for(mcp.call_tool("list_shapes", {}), 30)).content or [])
                    d = _json.loads(t[t.find("{"):t.rfind("}")+1]) if "{" in t else {}
                    shapes = d.get("shapes", []) if isinstance(d, dict) else []
                except Exception:  # noqa: BLE001
                    shapes = []
                shots = await asyncio.wait_for(_shoot(mcp), 120)
    except Exception as e:  # noqa: BLE001
        report += f"\n(캡처 단계 오류/시간초과: {type(e).__name__})"
    return report, shapes, shots


def build_capture(images: list[tuple[bytes, str]], text: str, model: str = "claude-opus-4-8") -> dict[str, Any]:
    """모델 빌드 → 스크린샷(다각도) + bbox 프록시 export(USD/STL). 전제: 토큰 + 라이브 페어링."""
    from ...settings import get_settings
    s = get_settings()
    if not s.trinix_ai_token:
        raise RuntimeError("TRINIX_AI_TOKEN 이 없습니다(.env).")
    report, shapes, shots = asyncio.run(_build_capture_async(images, text, model))
    if not shots and not shapes:
        raise RuntimeError("Trinix 응답 없음 — 에디터 탭이 녹색(연결)인지 확인하세요.\n보고:\n" + report[-700:])
    usd = stl = b""; table = []
    if shapes:
        usd, stl, table = _bbox_proxy(shapes)
    return {"report": report, "shots": shots, "shapes": table, "proxy_usd": usd, "proxy_stl": stl}


def build_and_shoot(images: list[tuple[bytes, str]], text: str, model: str = "claude-opus-4-8") -> dict[str, Any]:
    """모델 빌드 → 스크린샷(여러 각도). export(형상파일)는 Trinix 측 고장이라 제외.
    {report, shots:[(view,png_bytes)]}. 전제: TRINIX_AI_TOKEN + 라이브 페어링 세션."""
    from ...settings import get_settings
    s = get_settings()
    if not s.trinix_ai_token:
        raise RuntimeError("TRINIX_AI_TOKEN 이 없습니다(.env).")
    report, shots = asyncio.run(_build_and_shoot_async(images, text, model))
    if not shots:
        raise RuntimeError("스크린샷을 받지 못했습니다(페어링 세션·씬 확인).\n보고:\n" + report[-800:])
    return {"report": report, "shots": shots}


def preview_glb(step_bytes: bytes) -> bytes:
    """STEP → 뷰어용 GLB(파트 보존)."""
    scene = _load_step_scene(step_bytes)
    return scene.export(file_type="glb")


def merged_stl(step_bytes: bytes) -> bytes:
    """편의용 병합 STL(파트 정보 없음 — 빠른 확인/범용 용도)."""
    import trimesh

    scene = _load_step_scene(step_bytes)
    mesh = scene.dump(concatenate=True) if isinstance(scene, trimesh.Scene) else scene
    return mesh.export(file_type="stl")


def geometry_usd(step_bytes: bytes) -> bytes:
    """STEP → 형상만 담은 자기완결 USDA(파트별 Mesh). Isaac RTX 뷰어용(재질 없음).

    cascadio STEP 은 미터·Y-up → upAxis=Y, metersPerUnit=1 로 그대로 저작.
    조명은 isaac_render.py 가 추가하므로 여기선 불필요."""
    import numpy as np
    import trimesh
    from pxr import Gf, Usd, UsdGeom

    scene = _load_step_scene(step_bytes)
    if not isinstance(scene, trimesh.Scene):
        scene = trimesh.Scene(scene)

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    root = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(root.GetPrim())

    seen: dict[str, int] = {}
    for node in scene.graph.nodes_geometry:
        T, gname = scene.graph[node]
        geo = scene.geometry[gname]
        V = trimesh.transformations.transform_points(np.asarray(geo.vertices, float), T)
        F = np.asarray(geo.faces, int)
        if len(V) == 0 or len(F) == 0:
            continue
        safe = "".join(c if c.isalnum() else "_" for c in str(gname)) or "part"
        seen[safe] = seen.get(safe, 0) + 1
        nm = safe if seen[safe] == 1 else f"{safe}_{seen[safe]}"
        mesh = UsdGeom.Mesh.Define(stage, f"/World/{nm}")
        mesh.CreatePointsAttr([Gf.Vec3f(float(a), float(b), float(c)) for a, b, c in V])
        mesh.CreateFaceVertexCountsAttr([3] * len(F))
        mesh.CreateFaceVertexIndicesAttr([int(i) for i in F.reshape(-1)])
        mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    return stage.GetRootLayer().ExportToString().encode("utf-8")
