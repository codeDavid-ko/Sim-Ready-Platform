"""에셋 준비 워크플로우 — 3D 메시 → sim-ready USD(.usda) 변환·검증·등록.

입력:  3D 파일 업로드 (.glb/.gltf/.obj/.stl/.ply)
처리:  trimesh 로드 → Z-up/meters 정규화 → USD 저작
       (Mesh + UsdPhysics RigidBody/Collision/Mass) → 저장소 등록
출력:  변환 USD 다운로드 + 저장소 등록 + sim-ready 검증 리포트
흐름:  원샷 실행 (중간 승인 없음)

매니페스트 규약 v1 의 첫 레퍼런스 구현. 셸은 이 파일을 모른다 — registry 가
manifest.json + run() 만 보고 등록한다.
"""

from __future__ import annotations

import io
from pathlib import PurePath
from typing import Any

import numpy as np
import trimesh
from pxr import Gf, Tf, Usd, UsdGeom, UsdPhysics, Vt

_SUPPORTED = {"glb", "gltf", "obj", "stl", "ply", "step", "stp"}
_TRI_BUDGET = 200_000


def _load_mesh(file_bytes: bytes, ext: str) -> trimesh.Trimesh:
    if ext in {"step", "stp"}:
        # STEP 은 cascadio(파일 경로)로 로드가 안정적 → 임시파일 경유.
        import os
        import tempfile

        fd, path = tempfile.mkstemp(suffix=f".{ext}")
        with os.fdopen(fd, "wb") as f:
            f.write(file_bytes)
        try:
            loaded = trimesh.load(path)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
    else:
        loaded = trimesh.load(io.BytesIO(file_bytes), file_type=ext)
    if isinstance(loaded, trimesh.Scene):
        if not loaded.geometry:
            raise ValueError("씬에 메시 지오메트리가 없습니다.")
        mesh = loaded.dump(concatenate=True)
    else:
        mesh = loaded
    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError("메시로 변환할 수 없는 파일입니다.")
    if mesh.vertices.size == 0 or mesh.faces.size == 0:
        raise ValueError("빈 메시입니다(정점/면 없음).")
    return mesh


def _author_usd(
    verts: np.ndarray, faces: np.ndarray, bounds: np.ndarray, prim_name: str
) -> str:
    """정규화된 메시 + UsdPhysics 스키마로 USD 스테이지를 저작해 .usda 텍스트 반환."""
    stage = Usd.Stage.CreateInMemory("asset.usda")
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)

    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())

    # 리지드바디 + 질량은 바디 Xform 에, 충돌은 메시에 (Isaac 관례)
    body = UsdGeom.Xform.Define(stage, f"/World/{prim_name}")
    UsdPhysics.RigidBodyAPI.Apply(body.GetPrim())
    mass_api = UsdPhysics.MassAPI.Apply(body.GetPrim())
    mass_api.CreateMassAttr(1.0)

    geom = UsdGeom.Mesh.Define(stage, f"/World/{prim_name}/geom")
    gp = geom.GetPrim()
    geom.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(verts.astype(np.float32)))
    geom.CreateFaceVertexCountsAttr(
        Vt.IntArray.FromNumpy(np.full(len(faces), 3, dtype=np.int32))
    )
    geom.CreateFaceVertexIndicesAttr(
        Vt.IntArray.FromNumpy(faces.reshape(-1).astype(np.int32))
    )
    geom.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    geom.CreateExtentAttr(Vt.Vec3fArray.FromNumpy(bounds.astype(np.float32)))

    UsdPhysics.CollisionAPI.Apply(gp)
    mesh_col = UsdPhysics.MeshCollisionAPI.Apply(gp)
    mesh_col.CreateApproximationAttr(UsdPhysics.Tokens.convexHull)

    return stage.GetRootLayer().ExportToString()


def run(
    params: dict[str, Any],
    file_bytes: bytes | None = None,
    file_name: str | None = None,
    ctx: Any = None,
) -> dict[str, Any]:
    if not file_bytes:
        raise ValueError("3D 파일을 업로드하세요.")
    ext = (PurePath(file_name or "").suffix or "").lower().lstrip(".")
    if ext not in _SUPPORTED:
        raise ValueError(
            f"지원하지 않는 형식: .{ext or '?'} (지원: {', '.join(sorted(_SUPPORTED))})"
        )

    mesh = _load_mesh(file_bytes, ext)
    notes: list[str] = []

    # glTF/GLB 는 Y-up 관례 → Isaac Z-up 으로 회전
    if ext in {"glb", "gltf"}:
        mesh.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2.0, [1, 0, 0]))
        notes.append("Y-up→Z-up 회전 적용 (glTF 관례)")

    # 바운딩박스 중심을 원점으로 정렬
    bbox_center = mesh.bounds.mean(axis=0).astype(float)
    mesh.apply_translation(-bbox_center)
    notes.append(f"바운딩박스 중심 원점 정렬 (이동 {np.round(bbox_center, 4).tolist()})")

    verts = np.asarray(mesh.vertices, dtype=np.float32)
    faces = np.asarray(mesh.faces, dtype=np.int32)
    bounds = np.asarray(mesh.bounds, dtype=np.float32)
    extents = (mesh.bounds[1] - mesh.bounds[0]).astype(float)

    prim_name = Tf.MakeValidIdentifier(PurePath(file_name or "asset").stem) or "Asset"
    usda = _author_usd(verts, faces, bounds, prim_name)

    tri_count = int(len(faces))
    watertight = bool(mesh.is_watertight)
    max_dim = float(extents.max())

    checks = [
        {"key": "units_meters", "ok": True, "detail": "metersPerUnit = 1.0"},
        {"key": "up_axis_z", "ok": True, "detail": "stage upAxis = Z"},
        {"key": "centered", "ok": True, "detail": "원점 정렬됨"},
        {"key": "has_collision", "ok": True, "detail": "MeshCollisionAPI (convexHull)"},
        {"key": "has_rigid_body", "ok": True, "detail": "RigidBodyAPI + MassAPI(1.0)"},
        {
            "key": "watertight",
            "ok": watertight,
            "detail": "닫힌 메시" if watertight else "비폐쇄 메시 — 충돌 근사 품질 저하 가능",
        },
        {"key": "tri_budget", "ok": tri_count <= _TRI_BUDGET, "detail": f"{tri_count:,} tris"},
        {
            "key": "scale_sane",
            "ok": 0.01 <= max_dim <= 100.0,
            "detail": f"최대 치수 {max_dim:.3f} m",
        },
    ]
    # 필수 항목(변환 성립 여부)만으로 sim_ready 판정. watertight/tri/scale 은 경고성.
    required_keys = {"units_meters", "up_axis_z", "has_collision", "has_rigid_body"}
    sim_ready = all(c["ok"] for c in checks if c["key"] in required_keys)

    asset = None
    registered = False
    if ctx is not None:
        asset = ctx.register_asset(
            display_name=prim_name,
            filename=f"{prim_name}.usda",
            data=usda.encode("utf-8"),
            meta={
                "source": file_name,
                "tris": tri_count,
                "extents_m": [round(x, 4) for x in extents.tolist()],
            },
        )
        registered = True

    return {
        "summary": {
            "source_file": file_name,
            "format": ext,
            "vertices": int(len(verts)),
            "triangles": tri_count,
            "extents_m": [round(x, 4) for x in extents.tolist()],
            "watertight": watertight,
        },
        "normalization": notes,
        "checks": checks,
        "sim_ready": sim_ready,
        "asset": asset,
        "registered": registered,
        "usd_preview": "\n".join(usda.splitlines()[:40]),
    }
