"""mass_physics 파이프라인 — ndotsim 물성 추론을 Sim-Ready 플랫폼 카드로 구현.

흐름(Notion 사양):
  STEP/STL → 형상 정확 질량특성(trimesh, LLM 아님) → Claude Stage1(재질 분류)
  → Claude Stage2(접촉 물리값 + clamp) → mass=ρ×V · UsdPhysics 단일 .usd → usdchecker.

HARD RULE: mass·volume·inertia·CoM 은 형상에서 결정론적으로 계산한다. LLM 은 재질
(→density)과 접촉계수(friction/restitution)만 정한다.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from typing import Any

import numpy as np

from ..material_usd import common
from .reference import MATERIALS, TAXONOMY

_UNIT_TO_M = {"m": 1.0, "mm": 0.001, "cm": 0.01, "in": 0.0254}


# ───────────────────────── 1. 형상 정확 질량특성 ─────────────────────────
def _part_geometry(name: str, V_m: np.ndarray, F: np.ndarray) -> dict[str, Any]:
    """미터 좌표 메시 → 정확 부피/면적/관성/CoM + Stage1 features.

    watertight 면 mesh.volume(정확). 아니면 convex_hull 폴백(중공 과대 가능).
    trimesh moment_inertia 는 density=1 기준(= I_geom, 단위 m^5)."""
    import trimesh

    mesh = trimesh.Trimesh(vertices=V_m, faces=F, process=False)
    mn, mx = V_m.min(0), V_m.max(0)
    dims_m = (mx - mn)
    method = "mesh_exact"
    src = mesh
    if not (mesh.is_volume and mesh.volume > 1e-12):
        try:
            src = mesh.convex_hull
            method = "convex_hull"
        except Exception:  # noqa: BLE001
            src = None
            method = "bbox"
    if src is not None and getattr(src, "volume", 0) > 1e-12:
        volume = float(src.volume)
        com = [float(x) for x in src.center_mass]
        i_geom = np.asarray(src.moment_inertia, float)  # m^5 (density=1)
    else:  # bbox 폴백
        volume = float(dims_m[0] * dims_m[1] * dims_m[2])
        com = [float(x) for x in (mn + mx) / 2]
        lx, ly, lz = dims_m
        # 균일 직육면체 관성(density=1, mass=volume): (V/12)*(b^2+c^2) ...
        i_geom = np.diag([
            volume / 12.0 * (ly * ly + lz * lz),
            volume / 12.0 * (lx * lx + lz * lz),
            volume / 12.0 * (lx * lx + ly * ly),
        ])
    area = float(mesh.area) if mesh.area else 0.0
    dims_mm = [round(float(x) * 1000.0, 2) for x in dims_m]
    edges = sorted(d for d in dims_m if d > 0) or [0.0]
    thinness = round(float(edges[0] / edges[-1]), 4) if edges[-1] > 0 else 0.0
    apv = round(float(area / volume), 2) if volume > 0 else 0.0
    return {
        "name": name,
        "dims_mm": dims_mm,
        "dims_m": [float(x) for x in dims_m],
        "volume_m3": round(volume, 9),
        "area_m2": round(area, 6),
        "thinness": thinness,
        "area_per_volume_1pm": apv,
        "volume_method": method,
        "com_m": com,
        "inertia_geom_m5": i_geom.tolist(),
        "_V": V_m,
        "_F": F,
    }


def parse_geometry(file_bytes: bytes, file_name: str, in_units: str = "mm") -> list[dict[str, Any]]:
    """파일 → 파트별 정확 질량특성 리스트(미터 기준)."""
    scale = _UNIT_TO_M.get(in_units, 0.001)
    ext = os.path.splitext(file_name or "")[1].lower() or ".stl"
    fd, path = tempfile.mkstemp(suffix=ext)
    with os.fdopen(fd, "wb") as f:
        f.write(file_bytes)
    try:
        raw = common.load_parts(path)  # [(name, V(원본단위), F)]
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    if not raw:
        raise ValueError("형상에서 메시를 찾지 못했습니다.")
    parts = []
    seen: dict[str, int] = {}
    for name, V, F in raw:
        seen[name] = seen.get(name, 0) + 1
        nm = name if seen[name] == 1 else f"{name}_{seen[name]}"
        V_m = np.asarray(V, float) * scale
        parts.append(_part_geometry(nm, V_m, np.asarray(F, int)))
    return parts


# ───────────────────────── 2. Claude 추론 (Stage1 / Stage2) ─────────────────────────
_STAGE1_SYS = (
    "You are a mechanical-engineering material classifier. Given CAD parts (name + "
    "geometry only), assign each part the single most likely material from this FIXED "
    "taxonomy — never invent a material outside it:\n"
    + "\n".join(f"  - {k}: density {v['density']} kg/m^3" for k, v in MATERIALS.items())
    + "\n\nAn 'Assembly / product' line and optional 'Context' may precede the parts. When "
    "present, treat the product TYPE as a STRONG prior — e.g. a foldable crate/box or tote "
    "is almost always molded plastic (polypropylene/hdpe/abs), not metal — and let it "
    "override a generic name-only guess.\n"
    "Per part you also get shape cues: `thinness` (min/max bbox edge; near 0 = a thin "
    "panel/sheet/shell, not a solid billet), `area_per_volume_1pm` (large = thin-walled or "
    "hollow), and `volume_method` (`mesh_exact` = trustworthy volume; `convex_hull`/`bbox` = "
    "volume may be over-estimated for a hollow part). A thin large panel is consistent with "
    "BOTH a plastic enclosure wall and sheet metal — disambiguate using the product context.\n"
    "Reason from the part name, the product context, and size/shape. Always pick the closest "
    "taxonomy key even when unsure; express uncertainty via `confidence` (0-1), not by "
    "refusing. Keep `reasoning` to one short sentence. Return exactly one choice per part, "
    "echoing its part_index.\n\n"
    'Respond with ONLY a JSON object, no prose: {"choices":[{"part_index":int,'
    '"material":"<taxonomy key>","confidence":0..1,"reasoning":"one sentence"}]}'
)

_STAGE2_SYS = (
    "You are a contact-physics estimator for rigid-body simulation. For each CAD part you "
    "are given its material, geometry, and the ALLOWED RANGE [lo, hi] for each contact "
    "parameter (static_friction, dynamic_friction, restitution; dry-contact, PhysX "
    "RigidBodyMaterial convention).\n"
    "Pick one value per parameter, reasoning from the material and likely surface finish/use. "
    "You MUST stay inside each parameter's allowed range, and keep dynamic_friction <= "
    "static_friction. Keep `reasoning` to one short sentence. Return exactly one choice per "
    "part, echoing its part_index.\n\n"
    'Respond with ONLY a JSON object, no prose: {"choices":[{"part_index":int,'
    '"static_friction":float,"dynamic_friction":float,"restitution":float,'
    '"reasoning":"one sentence"}]}'
)


def _extract_json(text: str) -> dict[str, Any]:
    s = text.find("{")
    e = text.rfind("}")
    if s < 0 or e < 0:
        raise ValueError(f"LLM 응답에서 JSON 을 찾지 못함: {text[:200]}")
    return json.loads(text[s : e + 1])


async def _call_llm(
    system: str, user: str, api_key: str, oauth_token: str, model: str,
    images: list[tuple[bytes, str]] | None = None,
) -> dict[str, Any]:
    """구독(claude-agent-sdk) 우선, API 키 있으면 raw anthropic. forced JSON 파싱.
    images(참조 이미지)가 있으면 분류 프롬프트에 같이 넣는다(Stage1 재질 추론 정확도↑)."""
    imgs = images or []
    prompt = system + "\n\n" + user
    if api_key:
        from ..material_usd.pipeline import _classify_via_anthropic
        raw = _classify_via_anthropic(prompt, imgs, api_key)
    else:
        from ..material_usd.pipeline import _classify_via_agent
        raw = await _classify_via_agent(prompt, imgs, model)
    return _extract_json(raw)


def _stage1_user(parts: list[dict], context: str) -> str:
    feats = [
        {
            "part_index": i,
            "name": p["name"],
            "dims_mm": p["dims_mm"],
            "volume_m3": p["volume_m3"],
            "area_m2": p["area_m2"],
            "thinness": p["thinness"],
            "area_per_volume_1pm": p["area_per_volume_1pm"],
            "volume_method": p["volume_method"],
        }
        for i, p in enumerate(parts)
    ]
    head = (f"Context: {context}\n\n" if context else "") + "Parts:\n"
    return head + json.dumps(feats, ensure_ascii=False, indent=2)


def _stage2_user(parts: list[dict], materials: list[str]) -> str:
    feats = []
    for i, p in enumerate(parts):
        m = materials[i]
        ref = MATERIALS[m]
        feats.append({
            "part_index": i,
            "name": p["name"],
            "material": m,
            "dims_mm": p["dims_mm"],
            "allowed_ranges": {
                "static_friction": ref["static_friction"],
                "dynamic_friction": ref["dynamic_friction"],
                "restitution": ref["restitution"],
            },
        })
    return "Parts:\n" + json.dumps(feats, ensure_ascii=False, indent=2)


def _clamp(v: float, lo: float, hi: float) -> tuple[float, bool]:
    if v < lo:
        return lo, True
    if v > hi:
        return hi, True
    return v, False


async def infer(
    parts: list[dict[str, Any]],
    context: str = "",
    images: list[tuple[bytes, str]] | None = None,
    api_key: str = "",
    oauth_token: str = "",
    model: str = "claude-sonnet-4-6",
) -> dict[str, Any]:
    """Stage1(재질)+Stage2(접촉물리) 추론 → parts 에 material/density/friction/restitution 주입.
    images(참조 이미지)는 Stage1 재질 분류에만 사용. LLM 없으면 steel 기본 + range 중앙값 폴백."""
    n = len(parts)
    clamped: list[dict] = []
    if not (api_key or oauth_token):
        for p in parts:
            p["material"] = "steel"
            p["confidence"] = 0.0
            p["material_reasoning"] = "LLM 미사용(인증 없음): steel 기본값"
        mats = ["steel"] * n
    else:
        s1 = await _call_llm(_STAGE1_SYS, _stage1_user(parts, context), api_key, oauth_token, model, images=images)
        choice_by_idx = {int(c["part_index"]): c for c in s1.get("choices", [])}
        mats = []
        for i, p in enumerate(parts):
            c = choice_by_idx.get(i, {})
            mat = c.get("material")
            if mat not in MATERIALS:
                mat = "steel"
            p["material"] = mat
            p["confidence"] = float(c.get("confidence", 0.0))
            p["material_reasoning"] = c.get("reasoning", "")
            mats.append(mat)

    # density 는 reference 스칼라(추론 아님)
    for p, m in zip(parts, mats):
        p["density"] = MATERIALS[m]["density"]

    # Stage2 접촉물리
    if not (api_key or oauth_token):
        for p, m in zip(parts, mats):
            ref = MATERIALS[m]
            p["static_friction"] = round(sum(ref["static_friction"]) / 2, 3)
            p["dynamic_friction"] = round(sum(ref["dynamic_friction"]) / 2, 3)
            p["restitution"] = round(sum(ref["restitution"]) / 2, 3)
            p["physics_reasoning"] = "LLM 미사용: range 중앙값"
    else:
        s2 = await _call_llm(_STAGE2_SYS, _stage2_user(parts, mats), api_key, oauth_token, model)
        by_idx = {int(c["part_index"]): c for c in s2.get("choices", [])}
        for i, (p, m) in enumerate(zip(parts, mats)):
            ref = MATERIALS[m]
            c = by_idx.get(i, {})
            sf, c1 = _clamp(float(c.get("static_friction", sum(ref["static_friction"]) / 2)), *ref["static_friction"])
            df, c2 = _clamp(float(c.get("dynamic_friction", sum(ref["dynamic_friction"]) / 2)), *ref["dynamic_friction"])
            rest, c3 = _clamp(float(c.get("restitution", sum(ref["restitution"]) / 2)), *ref["restitution"])
            if df > sf:  # dynamic <= static 강제
                df = sf
                c2 = True
            p["static_friction"] = round(sf, 3)
            p["dynamic_friction"] = round(df, 3)
            p["restitution"] = round(rest, 3)
            p["physics_reasoning"] = c.get("reasoning", "")
            for key, was in (("static_friction", c1), ("dynamic_friction", c2), ("restitution", c3)):
                if was:
                    clamped.append({"part_index": i, "name": p["name"], "param": key})

    # mass = density × volume (HARD RULE)
    for p in parts:
        p["mass_kg"] = round(p["density"] * p["volume_m3"], 6)
    return {"clamped": clamped}


# ───────────────────────── 3. USD 오써링 (UsdPhysics) ─────────────────────────
def _diagonalize_inertia(i_geom_m5: list, density: float) -> tuple[list[float], list[float]]:
    """I = density × I_geom(m^5) 대칭텐서 → (diagonalInertia Vec3, principalAxes quat[w,x,y,z])."""
    I = np.asarray(i_geom_m5, float) * float(density)
    I = (I + I.T) / 2.0
    vals, vecs = np.linalg.eigh(I)  # 오름차순 고유값, 정규직교 고유벡터(열)
    R = vecs
    if np.linalg.det(R) < 0:  # 우수좌표계 보장
        R[:, 0] = -R[:, 0]
    # 회전행렬 → 쿼터니언 (w,x,y,z)
    tr = R[0, 0] + R[1, 1] + R[2, 2]
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        w = 0.25 * s
        x = (R[2, 1] - R[1, 2]) / s
        y = (R[0, 2] - R[2, 0]) / s
        z = (R[1, 0] - R[0, 1]) / s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = math.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
        w = (R[2, 1] - R[1, 2]) / s
        x = 0.25 * s
        y = (R[0, 1] + R[1, 0]) / s
        z = (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = math.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
        w = (R[0, 2] - R[2, 0]) / s
        x = (R[0, 1] + R[1, 0]) / s
        y = 0.25 * s
        z = (R[1, 2] + R[2, 1]) / s
    else:
        s = math.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
        w = (R[1, 0] - R[0, 1]) / s
        x = (R[0, 2] + R[2, 0]) / s
        y = (R[1, 2] + R[2, 1]) / s
        z = 0.25 * s
    return [float(max(v, 0.0)) for v in vals], [float(w), float(x), float(y), float(z)]


def author_usd(parts: list[dict[str, Any]], layout: str = "assembled") -> bytes:
    """parts(material/mass/friction 주입됨) → 단일 자기완결 .usda (base UsdPhysics)."""
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdPhysics.SetStageKilogramsPerUnit(stage, 1.0)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())

    scene = UsdPhysics.Scene.Define(stage, "/World/PhysicsScene")
    scene.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))
    scene.CreateGravityMagnitudeAttr(9.81)

    # 지면 (droptest 일 때 정착 확인용)
    ground = UsdGeom.Mesh.Define(stage, "/World/Ground")
    g = 5.0
    ground.CreatePointsAttr([Gf.Vec3f(-g, -g, 0), Gf.Vec3f(g, -g, 0), Gf.Vec3f(g, g, 0), Gf.Vec3f(-g, g, 0)])
    ground.CreateFaceVertexCountsAttr([4])
    ground.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    UsdPhysics.CollisionAPI.Apply(ground.GetPrim())

    mats_scope = UsdGeom.Scope.Define(stage, "/World/PhysicsMaterials")
    seen_mat: dict[str, Any] = {}

    drop_gap = 0.0
    for idx, p in enumerate(parts):
        safe = "".join(ch if ch.isalnum() else "_" for ch in p["name"]) or f"part_{idx}"
        ppath = f"/World/{safe}"
        xf = UsdGeom.Xform.Define(stage, ppath)
        prim = xf.GetPrim()
        UsdPhysics.RigidBodyAPI.Apply(prim)
        mass_api = UsdPhysics.MassAPI.Apply(prim)
        mass_api.CreateMassAttr(float(p["mass_kg"]))
        mass_api.CreateCenterOfMassAttr(Gf.Vec3f(*[float(x) for x in p["com_m"]]))
        diag, quat = _diagonalize_inertia(p["inertia_geom_m5"], p["density"])
        mass_api.CreateDiagonalInertiaAttr(Gf.Vec3f(diag[0], diag[1], diag[2]))
        mass_api.CreatePrincipalAxesAttr(Gf.Quatf(quat[0], quat[1], quat[2], quat[3]))

        if layout == "droptest":
            UsdGeom.XformCommonAPI(xf).SetTranslate((drop_gap, 0.0, float(p["dims_m"][2]) + 0.1))
            drop_gap += float(p["dims_m"][0]) + 0.2

        # 메시 (visual + collision 겸용)
        V = np.asarray(p["_V"], float)
        F = np.asarray(p["_F"], int)
        mesh = UsdGeom.Mesh.Define(stage, f"{ppath}/geom")
        mesh.CreatePointsAttr([Gf.Vec3f(float(a), float(b), float(c)) for a, b, c in V])
        mesh.CreateFaceVertexCountsAttr([3] * len(F))
        mesh.CreateFaceVertexIndicesAttr([int(x) for x in F.reshape(-1)])
        mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
        mcoll = UsdPhysics.CollisionAPI.Apply(mesh.GetPrim())
        mcoll.CreateCollisionEnabledAttr(True)
        mmesh = UsdPhysics.MeshCollisionAPI.Apply(mesh.GetPrim())
        mmesh.CreateApproximationAttr(UsdPhysics.Tokens.convexHull)

        # 물리 머티리얼 (재질별 1개 재사용)
        m = p["material"]
        if m not in seen_mat:
            mat = UsdShade.Material.Define(stage, f"/World/PhysicsMaterials/{m}")
            pm = UsdPhysics.MaterialAPI.Apply(mat.GetPrim())
            pm.CreateStaticFrictionAttr(float(p["static_friction"]))
            pm.CreateDynamicFrictionAttr(float(p["dynamic_friction"]))
            pm.CreateRestitutionAttr(float(p["restitution"]))
            seen_mat[m] = mat
        bind = UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim())
        bind.Bind(seen_mat[m], bindingStrength=UsdShade.Tokens.weakerThanDescendants, materialPurpose="physics")

    layer = stage.GetRootLayer()
    return layer.ExportToString().encode("utf-8")


def preview_glb(parts: list[dict[str, Any]]) -> bytes:
    """파트 형상(미터) → 뷰어용 GLB. 재질 없음(물성 카드는 질량/물리에 집중)."""
    import trimesh

    scene = trimesh.Scene()
    for p in parts:
        scene.add_geometry(
            trimesh.Trimesh(vertices=np.asarray(p["_V"], float), faces=np.asarray(p["_F"], int)),
            node_name="".join(ch if ch.isalnum() else "_" for ch in p["name"]) or "part",
        )
    return scene.export(file_type="glb")


def parts_table(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """직렬화 가능한 결과 테이블(메시 데이터·텐서 제외)."""
    return [
        {
            "name": p["name"],
            "material": p.get("material"),
            "confidence": p.get("confidence"),
            "density": p.get("density"),
            "volume_m3": p["volume_m3"],
            "mass_kg": p.get("mass_kg"),
            "dims_mm": p["dims_mm"],
            "volume_method": p["volume_method"],
            "static_friction": p.get("static_friction"),
            "dynamic_friction": p.get("dynamic_friction"),
            "restitution": p.get("restitution"),
            "material_reasoning": p.get("material_reasoning", ""),
            "physics_reasoning": p.get("physics_reasoning", ""),
        }
        for p in parts
    ]


def validate_usd(usd_bytes: bytes) -> dict[str, Any]:
    """usdchecker(ComplianceChecker) — 위반 리스트(빈=통과)."""
    try:
        from pxr import Sdf, Usd, UsdUtils
    except Exception as e:  # noqa: BLE001
        return {"ran": False, "error": str(e), "violations": []}
    fd, path = tempfile.mkstemp(suffix=".usda")
    with os.fdopen(fd, "wb") as f:
        f.write(usd_bytes)
    try:
        checker = UsdUtils.ComplianceChecker(
            arkit=False, skipARKitRootLayerCheck=True, verbose=False
        )
        checker.CheckCompliance(path)
        errs = list(checker.GetErrors()) + list(checker.GetFailedChecks())
        return {"ran": True, "violations": [str(e) for e in errs]}
    except Exception as e:  # noqa: BLE001
        return {"ran": False, "error": str(e), "violations": []}
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
