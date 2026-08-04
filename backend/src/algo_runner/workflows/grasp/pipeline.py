"""그래스프(파지) 카드 — USD 에 grasp_identifier_curve(BasisCurves) 를 넣는다.

NVIDIA SimReady GSP.001 은 'grasp_identifier' 로 시작하는 BasisCurves(≥2점)를 grasp 로 인식한다.
이 카드는 ① 형상을 3D 로 보여주고 ② Claude 가 파지 위치(두 점=그리퍼가 닫히는 선)를 추론하며
③ 사람이 화면에서 점을 클릭해 옮긴 뒤 ④ 그 두 점으로 커브를 USD 에 써서 자기완결 USD 로 낸다.

좌표 규약: 프런트/AI 가 다루는 점은 모두 '월드 미터'. USD 에 쓸 때 스테이지 단위(/mpu)로 변환한다
(articulation 과 동일 — extract 메시도 월드 미터로 내려보냄)."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import PurePath
from typing import Any

import numpy as np

_USD_EXT = {".usd", ".usda", ".usdc", ".usdz"}


def extract(file_bytes: bytes, name: str) -> tuple[list[dict], list[dict], str | None, str]:
    """관절 카드의 추출기를 재사용 — (tree, meshes(월드 미터), note, up_axis)."""
    from ..articulation.pipeline import extract as _ex
    return _ex(file_bytes, name)


# ───────────────────────── AI(Claude) 파지점 추론 ─────────────────────────
_AI_GRASP_SYS = (
    "You are a robotics grasping expert placing ONE grasp vector for a single rigid asset so a "
    "PARALLEL-JAW gripper can pick it up or operate it. You are given the asset's parts, each with a "
    "world-space axis-aligned bounding box in METERS (center_m, size_m), its USD prim path, and "
    "(when known) the part name.\n"
    "A grasp vector is a LINE of TWO points (start, end). PER THE NVIDIA SimReady SPEC this line MUST "
    "PASS THROUGH (intersect) the object, and the gripper ALIGNS its closing axis TO this line and "
    "CLOSES ALONG it: the two fingers sit at the TWO ENDPOINTS and squeeze toward each other ALONG "
    "the line. Therefore:\n"
    "- The two points are the TWO CONTACT POINTS where the gripper fingers touch, on OPPOSITE sides "
    "of the graspable feature. The line CROSSES THROUGH the feature.\n"
    "- The line LENGTH = the gripper opening = the WIDTH of the feature at that spot (typically a few "
    "cm — small enough for a gripper to span). Do NOT make it span the whole object, and do NOT lay "
    "it ALONG a handle's length.\n"
    "- Pick a graspable feature (handle bar, knob, lever, rim, lip, thin panel edge). Put the two "
    "points on its two OPPOSITE faces so the line passes through it across its SMALLEST graspable "
    "width. For a long/round handle bar, cross THROUGH the bar (perpendicular to the bar's long "
    "axis), centered along it — NOT from one end to the other.\n"
    "- If there is no distinct feature, cross through the whole asset along its SMALLEST horizontal "
    "width at a sensible height.\n"
    "- Prefer named features: a part named like handle/grip/knob/lever/bar/hook is the grasp target.\n"
    "RULES:\n"
    "- points = [[x,y,z],[x,y,z]] in WORLD METERS (two distinct points); the segment must intersect "
    "the object (cross through it), not float beside it.\n"
    "- part = the USD path (verbatim from input) of the part you grasped, or its name; \"\" if whole asset.\n"
    "- Keep both points within or on the asset's bounds.\n"
    "Return STRICT JSON ONLY, no prose:\n"
    '{"points":[[x,y,z],[x,y,z]],"part":"<path or name or empty>","reason":"<short why>"}'
)


def _placeholder_points(bodies: list[dict]) -> list[list[float]]:
    """AI 실패 시 — 전체 bbox 중앙을 가장 짧은 가로폭으로 가로지르는 선(미터)."""
    if not bodies:
        return [[0.0, 0.0, 0.0], [0.1, 0.0, 0.0]]
    lo = np.min([[b["center_m"][i] - b["size_m"][i] / 2 for i in range(3)] for b in bodies], axis=0)
    hi = np.max([[b["center_m"][i] + b["size_m"][i] / 2 for i in range(3)] for b in bodies], axis=0)
    span = hi - lo
    ax = int(np.argmin(span))  # 가장 짧은 축(그리퍼가 span 가능한 폭)을 가로지름
    c = (lo + hi) / 2
    p0 = c.copy(); p1 = c.copy()
    p0[ax] = lo[ax]; p1[ax] = hi[ax]
    return [[round(float(x), 5) for x in p0], [round(float(x), 5) for x in p1]]


def _snap_to_surface(meshes: list[dict], pts: list[list[float]]) -> list[list[float]]:
    """각 점을 형상의 가장 가까운 '정점'으로 스냅(공중에 뜨지 않게). rtree 불필요(정점 거리만).
    표면 정점에 정확히 올려 메시 위에 닿게 한다. 실패 시 원본 반환."""
    try:
        allV = []
        for m in meshes:
            V = np.asarray(m["vertices"], float).reshape(-1, 3)
            if len(V):
                allV.append(V)
        if not allV:
            return pts
        P = np.vstack(allV)
        out = []
        for p in pts:
            d = np.linalg.norm(P - np.asarray(p, float), axis=1)
            out.append([round(float(x), 5) for x in P[int(np.argmin(d))]])
        return out
    except Exception:  # noqa: BLE001
        return pts


async def ai_suggest_grasp(
    file_bytes: bytes, name: str, *, context: str = "",
    api_key: str = "", oauth_token: str = "", model: str = "claude-opus-4-8",
) -> tuple[list[list[float]], str, str | None, int]:
    """Claude 가 부품 AABB 를 보고 파지 두 점(월드 미터)+대상부품+이유를 추론.
    반환 points 는 프런트가 3D 에 점 두 개로 띄워 검토·수정 후 build 한다."""
    if not (api_key or oauth_token):
        raise ValueError("AI 파지점 추론용 Claude 자격증명이 없습니다(.env CLAUDE_CODE_OAUTH_TOKEN).")
    from ..articulation.pipeline import _ai_bodies
    from ..mass_physics.pipeline import _call_llm

    tree, meshes, _note, _up = extract(file_bytes, name)
    bodies = _ai_bodies(tree, meshes)
    user = ((f"Context: {context}\n\n" if context else "")
            + f"Asset has {len(bodies)} candidate parts. World AABB per part (meters):\n"
            + json.dumps(bodies, ensure_ascii=False))
    data = await _call_llm(_AI_GRASP_SYS, user, api_key, oauth_token, model)

    pts_raw = data.get("points") if isinstance(data, dict) else None
    points: list[list[float]] | None = None
    if isinstance(pts_raw, (list, tuple)) and len(pts_raw) >= 2:
        try:
            cand = [[float(pts_raw[k][0]), float(pts_raw[k][1]), float(pts_raw[k][2])] for k in range(2)]
            if cand[0] != cand[1]:
                points = [[round(x, 5) for x in p] for p in cand]
        except (TypeError, ValueError, IndexError):
            points = None
    part = str((data.get("part") if isinstance(data, dict) else "") or "")
    reason = str((data.get("reason") if isinstance(data, dict) else "") or "")[:300]
    note = None
    if points is None:
        points = _placeholder_points(bodies)
        note = "AI가 파지 위치를 명확히 잡지 못해 전체 bbox 가운데를 가로지르는 임시 두 점을 넣었습니다. 화면에서 손잡이 등 실제 파지 위치로 옮겨주세요."
    points = _snap_to_surface(meshes, points)   # 공중에 뜨지 않게 형상 표면으로 스냅
    return points, part, (reason or note), len(bodies)


# ───────────────────────── grasp 커브 저작 ─────────────────────────
def _add_grasp_curve(stage: Any, points: list[list[float]]) -> tuple[str, float]:
    """열린 stage 에 grasp_identifier_curve(2점 linear) 추가 + 첫 머티리얼 바인딩. (grasp_path, seg_len_stage)."""
    from pxr import Gf, UsdGeom, UsdShade

    mpu = UsdGeom.GetStageMetersPerUnit(stage) or 1.0
    dp = stage.GetDefaultPrim()
    base = dp.GetPath().pathString if (dp and dp.IsValid()) else ""
    su = [[float(c) / mpu for c in p[:3]] for p in points[:2]]   # 월드 미터 → 스테이지 단위
    gpath = f"{base}/grasp_identifier_curve"
    if stage.GetPrimAtPath(gpath) and stage.GetPrimAtPath(gpath).IsValid():
        stage.RemovePrim(gpath)
    curve = UsdGeom.BasisCurves.Define(stage, gpath)
    curve.CreateTypeAttr(UsdGeom.Tokens.linear)
    curve.CreatePointsAttr([Gf.Vec3f(*p) for p in su])
    curve.CreateCurveVertexCountsAttr([2])
    seg = float(np.linalg.norm(np.array(su[1]) - np.array(su[0]))) or 1.0
    w = max(seg * 0.03, 1e-4)
    curve.CreateWidthsAttr([w, w]); curve.SetWidthsInterpolation(UsdGeom.Tokens.vertex)
    xs = [p[0] for p in su]; ys = [p[1] for p in su]; zs = [p[2] for p in su]
    curve.CreateExtentAttr([Gf.Vec3f(min(xs), min(ys), min(zs)), Gf.Vec3f(max(xs), max(ys), max(zs))])
    mats = [p for p in stage.Traverse() if p.IsA(UsdShade.Material)]
    if mats:
        UsdShade.MaterialBindingAPI.Apply(curve.GetPrim()).Bind(UsdShade.Material(mats[0]))
    return gpath, seg


def author_grasp(file_bytes: bytes, name: str, points: list[list[float]]) -> tuple[bytes, str, dict[str, Any]]:
    """USD 에 grasp_identifier_curve(2점 linear BasisCurves) 추가 → 자기완결. points=월드 미터 2점.

    usdz 입력은 '풀어서 내부 .usd 만 편집 → 재패키징' 해서 기존 ./materials 앵커(MDL/텍스처)를
    보존한다(re-export 후 _finalize 로 묶으면 입력 usdz 가 통째로 중첩돼 앵커가 깨져 VM.MDL.001/AA.001
    이 뜬다). usd/usda/usdc 입력은 stage 편집 후 _finalize 로 외부 의존을 묶는다."""
    from pxr import Sdf, Usd, UsdGeom, UsdUtils

    ext = PurePath(name).suffix.lower()
    if ext not in _USD_EXT:
        raise ValueError("그래스프는 USD/USDZ 에만 넣을 수 있습니다(STEP 은 먼저 USD로 변환하세요).")
    if not (isinstance(points, list) and len(points) >= 2):
        raise ValueError("파지점 두 개(points)가 필요합니다.")

    # ── usdz: 풀어서 내부 .usd 만 편집 → 재패키징(앵커 보존) ──
    if ext == ".usdz":
        import glob
        import zipfile
        wd = tempfile.mkdtemp()
        zin = os.path.join(wd, "in.usdz")
        with open(zin, "wb") as f:
            f.write(file_bytes)
        with zipfile.ZipFile(zin) as z:
            z.extractall(wd)
        os.remove(zin)
        cands = sorted(glob.glob(os.path.join(wd, "*.usd")) + glob.glob(os.path.join(wd, "*.usda")) + glob.glob(os.path.join(wd, "*.usdc")))
        if not cands:
            raise RuntimeError("usdz 안에서 메인 USD 를 찾지 못했습니다.")
        main = cands[0]
        stage = Usd.Stage.Open(main)
        if stage is None:
            raise RuntimeError("usdz 내부 USD 를 열지 못했습니다.")
        gpath, seg = _add_grasp_curve(stage, points)
        mpu = UsdGeom.GetStageMetersPerUnit(stage) or 1.0
        stage.GetRootLayer().Save()
        outz = os.path.join(wd, "grasp_out.usdz")
        UsdUtils.CreateNewUsdzPackage(Sdf.AssetPath(main), outz)
        with open(outz, "rb") as f:
            out = f.read()
        info = {"points_m": [[round(float(c), 5) for c in p[:3]] for p in points[:2]],
                "grasp_path": gpath, "length_m": round(seg * mpu, 4)}
        return out, ".usdz", info

    # ── usd/usda/usdc: stage 편집 → 자기완결화 ──
    from ..articulation.pipeline import _finalize_self_contained
    fd, path = tempfile.mkstemp(suffix=ext)
    with os.fdopen(fd, "wb") as f:
        f.write(file_bytes)
    fd2, outp = tempfile.mkstemp(suffix=".usd")
    os.close(fd2)
    try:
        stage = Usd.Stage.Open(path)
        if stage is None:
            raise RuntimeError("입력 USD 를 열지 못했습니다.")
        mpu = UsdGeom.GetStageMetersPerUnit(stage) or 1.0
        gpath, seg = _add_grasp_curve(stage, points)
        stage.Export(outp)
        data, oext = _finalize_self_contained(outp)
        info = {"points_m": [[round(float(c), 5) for c in p[:3]] for p in points[:2]],
                "grasp_path": gpath, "length_m": round(seg * mpu, 4)}
        return data, oext, info
    finally:
        for p in (path, outp):
            try:
                os.remove(p)
            except OSError:
                pass
