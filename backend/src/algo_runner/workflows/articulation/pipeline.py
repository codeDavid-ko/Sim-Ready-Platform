"""articulation 파이프라인 — STEP/USD → 부품 추출 → 관절(UsdPhysics Joint) 부여 USD.

뷰어용 GLB(부품별 노드) + 부품 메타(이름·bbox·centroid)를 주고, 프런트에서 정의한
관절 목록(revolute/prismatic/fixed + 축 + 피벗)을 받아 UsdPhysics 조인트를 저작한다.
좌표는 미터로 정규화(STEP=cascadio 미터, USD=metersPerUnit 반영). 바디는 항등 변환 +
월드좌표 메시 → 조인트 localPos0/1 = 피벗(월드).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import PurePath
from typing import Any

import numpy as np

from ..material_usd import common

_SUPPORTED = {".step", ".stp", ".usd", ".usda", ".usdc", ".usdz"}


def _meters_scale(path: str, ext: str) -> float:
    if ext in {".usd", ".usda", ".usdc", ".usdz"}:
        try:
            from pxr import Usd, UsdGeom
            mpu = UsdGeom.GetStageMetersPerUnit(Usd.Stage.Open(path)) or 1.0
            return float(mpu)
        except Exception:  # noqa: BLE001
            return 1.0
    return 1.0  # STEP via cascadio → 이미 미터


def parse_parts(file_bytes: bytes, name: str) -> list[tuple[str, np.ndarray, np.ndarray]]:
    """파일 → [(part_name, V(미터), F)] (cascadio STEP / pxr USD)."""
    ext = PurePath(name).suffix.lower()
    if ext not in _SUPPORTED:
        raise ValueError(f"STEP 또는 USD만 지원합니다. 받은: {ext or '?'}")
    fd, path = tempfile.mkstemp(suffix=ext)
    with os.fdopen(fd, "wb") as f:
        f.write(file_bytes)
    try:
        raw = common.load_parts(path)
        scale = _meters_scale(path, ext)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    if not raw:
        raise ValueError("형상에서 부품(메시)을 찾지 못했습니다.")
    parts, seen = [], {}
    for nm, V, F in raw:
        seen[nm] = seen.get(nm, 0) + 1
        unm = nm if seen[nm] == 1 else f"{nm}_{seen[nm]}"
        parts.append((unm, np.asarray(V, float) * scale, np.asarray(F, int)))
    return parts


def parts_meta(parts: list[tuple[str, np.ndarray, np.ndarray]]) -> list[dict[str, Any]]:
    out = []
    for nm, V, _F in parts:
        mn, mx = V.min(0), V.max(0)
        c = (mn + mx) / 2
        out.append({
            "name": nm,
            "centroid_m": [round(float(x), 5) for x in c],
            "size_mm": [round(float(x) * 1000, 1) for x in (mx - mn)],
            "bbox_min_m": [round(float(x), 5) for x in mn],
            "bbox_max_m": [round(float(x), 5) for x in mx],
        })
    return out


def viewer_glb(parts: list[tuple[str, np.ndarray, np.ndarray]]) -> bytes:
    import trimesh

    scene = trimesh.Scene()
    for nm, V, F in parts:
        safe = "".join(c if c.isalnum() else "_" for c in nm) or "part"
        scene.add_geometry(trimesh.Trimesh(vertices=V, faces=F), node_name=safe, geom_name=safe)
    return scene.export(file_type="glb")


def _safe(nm: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in nm) or "part"


def author_usd(parts: list[tuple[str, np.ndarray, np.ndarray]], joints: list[dict[str, Any]]) -> tuple[bytes, list[dict[str, Any]]]:
    """부품 메시 + UsdPhysics 조인트 USD(.usda). joints 각 항목:
    {type: revolute|prismatic|fixed, parent: <name|''=world>, child: <name>,
     axis: 'X'|'Y'|'Z', pivot: [x,y,z](m)|null}."""
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdPhysics.SetStageKilogramsPerUnit(stage, 1.0)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    scene = UsdPhysics.Scene.Define(stage, "/World/PhysicsScene")
    scene.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))
    scene.CreateGravityMagnitudeAttr(9.81)

    path_by_name: dict[str, str] = {}
    centroid_by_name: dict[str, np.ndarray] = {}
    for nm, V, F in parts:
        safe = _safe(nm)
        ppath = f"/World/{safe}"
        xf = UsdGeom.Xform.Define(stage, ppath)
        UsdPhysics.RigidBodyAPI.Apply(xf.GetPrim())
        mesh = UsdGeom.Mesh.Define(stage, f"{ppath}/geom")
        mesh.CreatePointsAttr([Gf.Vec3f(float(a), float(b), float(c)) for a, b, c in V])
        mesh.CreateFaceVertexCountsAttr([3] * len(F))
        mesh.CreateFaceVertexIndicesAttr([int(i) for i in F.reshape(-1)])
        mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
        UsdPhysics.CollisionAPI.Apply(mesh.GetPrim())
        UsdPhysics.MeshCollisionAPI.Apply(mesh.GetPrim()).CreateApproximationAttr(UsdPhysics.Tokens.convexHull)
        path_by_name[nm] = ppath
        centroid_by_name[nm] = (V.min(0) + V.max(0)) / 2

    UsdGeom.Scope.Define(stage, "/World/Joints")
    authored = []
    for i, j in enumerate(joints):
        child = j.get("child")
        if child not in path_by_name:
            continue
        jtype = (j.get("type") or "revolute").lower()
        axis = (j.get("axis") or "Z").upper()
        if axis not in ("X", "Y", "Z"):
            axis = "Z"
        pivot = j.get("pivot")
        piv = np.asarray(pivot, float) if pivot else centroid_by_name[child]
        jp = f"/World/Joints/joint_{i}"
        if jtype == "prismatic":
            J = UsdPhysics.PrismaticJoint.Define(stage, jp)
            J.CreateAxisAttr(axis)
        elif jtype == "fixed":
            J = UsdPhysics.FixedJoint.Define(stage, jp)
        else:
            jtype = "revolute"
            J = UsdPhysics.RevoluteJoint.Define(stage, jp)
            J.CreateAxisAttr(axis)
        parent = j.get("parent") or ""
        if parent and parent in path_by_name:
            J.CreateBody0Rel().SetTargets([path_by_name[parent]])
        J.CreateBody1Rel().SetTargets([path_by_name[child]])
        # 바디는 항등 변환 + 월드좌표 메시 → 로컬프레임 == 월드프레임
        J.CreateLocalPos0Attr(Gf.Vec3f(float(piv[0]), float(piv[1]), float(piv[2])))
        J.CreateLocalPos1Attr(Gf.Vec3f(float(piv[0]), float(piv[1]), float(piv[2])))
        authored.append({"name": f"joint_{i}", "type": jtype, "axis": axis,
                         "parent": parent or "(world)", "child": child})

    return stage.GetRootLayer().ExportToString().encode("utf-8"), authored
