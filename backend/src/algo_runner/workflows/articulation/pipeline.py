"""articulation 파이프라인 — STEP/USD → 계층(트리) + 메시 → 관절(UsdPhysics) USD.

USD 의 실제 prim 계층(Xform/Mesh 트리)을 추출해 프런트에 주고, 사용자가 트리에서 고른
노드(서브트리)를 하나의 바디로 묶어 그 사이에 조인트를 건다. 메시는 월드좌표(미터)로
보내 프런트 Three.js 와 백엔드가 같은 좌표계를 쓴다(피벗/축 일치).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import PurePath
from typing import Any

import numpy as np

_USD_EXT = {".usd", ".usda", ".usdc", ".usdz"}
_STEP_EXT = {".step", ".stp"}
_SUPPORTED = _USD_EXT | _STEP_EXT


def _safe(s: str) -> str:
    r = "".join(c if c.isalnum() else "_" for c in str(s)).strip("_") or "node"
    if r[0].isdigit():        # USD prim 이름은 숫자로 시작 불가
        r = "n_" + r
    return r


# ───────────────────────── 추출 (트리 + 월드 메시) ─────────────────────────
def _extract_usd(path: str) -> tuple[list[dict], list[dict]]:
    from pxr import Gf, Usd, UsdGeom

    stage = Usd.Stage.Open(path)
    mpu = UsdGeom.GetStageMetersPerUnit(stage) or 1.0
    xcache = UsdGeom.XformCache()
    meshes: list[dict] = []

    def world_mesh(prim) -> dict | None:
        m = UsdGeom.Mesh(prim)
        pts = m.GetPointsAttr().Get()
        counts = m.GetFaceVertexCountsAttr().Get()
        idx = m.GetFaceVertexIndicesAttr().Get()
        if not pts or not counts or not idx:
            return None
        M = xcache.GetLocalToWorldTransform(prim)
        V = np.array([[*M.Transform(Gf.Vec3d(p[0], p[1], p[2]))] for p in pts], float) * mpu
        faces, o = [], 0
        for c in counts:
            for k in range(1, c - 1):
                faces.append([idx[o], idx[o + k], idx[o + k + 1]])
            o += c
        return {"path": str(prim.GetPath()), "name": prim.GetName(),
                "vertices": [round(float(x), 5) for x in V.reshape(-1).tolist()],
                "faces": [int(i) for i in np.asarray(faces, int).reshape(-1).tolist()]}

    def node(prim) -> dict:
        if prim.IsA(UsdGeom.Mesh):
            wm = world_mesh(prim)
            if wm:
                meshes.append(wm)
        return {"path": str(prim.GetPath()), "name": prim.GetName() or "/",
                "type": str(prim.GetTypeName()) or "Xform",
                "children": [node(c) for c in prim.GetChildren()]}

    root = stage.GetDefaultPrim() or stage.GetPseudoRoot()
    tree = [node(c) for c in root.GetChildren()] if not root.IsA(UsdGeom.Mesh) else [node(root)]
    return tree, meshes


def _extract_step(path: str) -> tuple[list[dict], list[dict]]:
    import trimesh

    scene = trimesh.load(path)
    if not isinstance(scene, trimesh.Scene):
        scene = trimesh.Scene(scene)
    meshes, children = [], []
    seen: dict[str, int] = {}
    for nodename in scene.graph.nodes_geometry:
        T, gname = scene.graph[nodename]
        geo = scene.geometry[gname]
        V = trimesh.transformations.transform_points(np.asarray(geo.vertices, float), T)
        base = _safe(gname)
        seen[base] = seen.get(base, 0) + 1
        nm = base if seen[base] == 1 else f"{base}_{seen[base]}"
        p = f"/{nm}"
        meshes.append({"path": p, "name": nm,
                       "vertices": [round(float(x), 5) for x in V.reshape(-1).tolist()],
                       "faces": [int(i) for i in np.asarray(geo.faces, int).reshape(-1).tolist()]})
        children.append({"path": p, "name": nm, "type": "Mesh", "children": []})
    tree = [{"path": "/root", "name": "root", "type": "Xform", "children": children}]
    return tree, meshes


def extract(file_bytes: bytes, name: str) -> tuple[list[dict], list[dict]]:
    ext = PurePath(name).suffix.lower()
    if ext not in _SUPPORTED:
        raise ValueError(f"STEP 또는 USD만 지원합니다. 받은: {ext or '?'}")
    fd, path = tempfile.mkstemp(suffix=ext)
    with os.fdopen(fd, "wb") as f:
        f.write(file_bytes)
    try:
        tree, meshes = _extract_usd(path) if ext in _USD_EXT else _extract_step(path)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    if not meshes:
        raise ValueError("형상에서 메시를 찾지 못했습니다.")
    return tree, meshes


# ───────────────────────── 관절 USD 저작 ─────────────────────────
def _under(mesh_path: str, node_path: str) -> bool:
    return mesh_path == node_path or mesh_path.startswith(node_path.rstrip("/") + "/")


def author_usd(meshes: list[dict], joints: list[dict[str, Any]]) -> tuple[bytes, list[dict[str, Any]]]:
    """meshes(월드 미터) + joints → 바디(선택 서브트리)별 RigidBody + UsdPhysics 조인트 USD.

    조인트가 참조하는 노드 path 각각을 하나의 바디로 묶는다(그 path 아래 메시들). 메시는
    가장 깊은(구체적인) 사용 path 에 배속, 어디에도 안 들면 정적 /World/rest 로."""
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    used = []
    for j in joints:
        for key in ("parent", "child"):
            p = j.get(key)
            if p and p not in used:
                used.append(p)

    def body_of(mesh_path: str) -> str | None:
        cands = [u for u in used if _under(mesh_path, u)]
        return max(cands, key=len) if cands else None

    groups: dict[str, list[dict]] = {u: [] for u in used}
    rest: list[dict] = []
    for m in meshes:
        b = body_of(m["path"])
        (groups[b] if b is not None else rest).append(m)

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdPhysics.SetStageKilogramsPerUnit(stage, 1.0)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    scene = UsdPhysics.Scene.Define(stage, "/World/PhysicsScene")
    scene.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))
    scene.CreateGravityMagnitudeAttr(9.81)

    def add_mesh(parent_path: str, m: dict, i: int) -> None:
        V = np.asarray(m["vertices"], float).reshape(-1, 3)
        F = np.asarray(m["faces"], int).reshape(-1, 3)
        g = UsdGeom.Mesh.Define(stage, f"{parent_path}/{_safe(m['name'])}_{i}")
        g.CreatePointsAttr([Gf.Vec3f(float(a), float(b), float(c)) for a, b, c in V])
        g.CreateFaceVertexCountsAttr([3] * len(F))
        g.CreateFaceVertexIndicesAttr([int(x) for x in F.reshape(-1)])
        g.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
        UsdPhysics.CollisionAPI.Apply(g.GetPrim())
        UsdPhysics.MeshCollisionAPI.Apply(g.GetPrim()).CreateApproximationAttr(UsdPhysics.Tokens.convexHull)

    body_prim: dict[str, str] = {}
    body_centroid: dict[str, np.ndarray] = {}
    for u in used:
        safe = _safe(u)
        bp = f"/World/{safe}"
        xf = UsdGeom.Xform.Define(stage, bp)
        UsdPhysics.RigidBodyAPI.Apply(xf.GetPrim())
        allV = []
        for i, m in enumerate(groups[u]):
            add_mesh(bp, m, i)
            allV.append(np.asarray(m["vertices"], float).reshape(-1, 3))
        body_prim[u] = bp
        if allV:
            P = np.vstack(allV)
            body_centroid[u] = (P.min(0) + P.max(0)) / 2
        else:
            body_centroid[u] = np.zeros(3)

    if rest:
        rx = UsdGeom.Xform.Define(stage, "/World/rest")
        for i, m in enumerate(rest):
            add_mesh("/World/rest", m, i)

    UsdGeom.Scope.Define(stage, "/World/Joints")
    authored = []
    for i, j in enumerate(joints):
        child = j.get("child")
        if child not in body_prim:
            continue
        jtype = (j.get("type") or "revolute").lower()
        axis = (j.get("axis") or "Z").upper()
        axis = axis if axis in ("X", "Y", "Z") else "Z"
        piv = np.asarray(j["pivot"], float) if j.get("pivot") else body_centroid[child]
        jp = f"/World/Joints/joint_{i}"
        if jtype == "prismatic":
            J = UsdPhysics.PrismaticJoint.Define(stage, jp); J.CreateAxisAttr(axis)
        elif jtype == "fixed":
            J = UsdPhysics.FixedJoint.Define(stage, jp)
        else:
            jtype = "revolute"; J = UsdPhysics.RevoluteJoint.Define(stage, jp); J.CreateAxisAttr(axis)
        parent = j.get("parent") or ""
        if parent and parent in body_prim:
            J.CreateBody0Rel().SetTargets([body_prim[parent]])
        J.CreateBody1Rel().SetTargets([body_prim[child]])
        J.CreateLocalPos0Attr(Gf.Vec3f(float(piv[0]), float(piv[1]), float(piv[2])))
        J.CreateLocalPos1Attr(Gf.Vec3f(float(piv[0]), float(piv[1]), float(piv[2])))
        lo, hi = j.get("lower"), j.get("upper")
        if jtype in ("revolute", "prismatic") and lo is not None and hi is not None:
            J.CreateLowerLimitAttr(float(lo)); J.CreateUpperLimitAttr(float(hi))
        authored.append({"name": f"joint_{i}", "type": jtype, "axis": axis,
                         "parent": parent or "(world)", "child": child, "lower": lo, "upper": hi})

    return stage.GetRootLayer().ExportToString().encode("utf-8"), authored


def viewer_glb(meshes: list[dict]) -> bytes:
    import trimesh

    scene = trimesh.Scene()
    for m in meshes:
        V = np.asarray(m["vertices"], float).reshape(-1, 3)
        F = np.asarray(m["faces"], int).reshape(-1, 3)
        scene.add_geometry(trimesh.Trimesh(vertices=V, faces=F), node_name=_safe(m["name"]))
    return scene.export(file_type="glb")
