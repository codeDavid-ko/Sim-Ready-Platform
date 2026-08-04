"""결과 미리보기용 PBR GLB 빌더 (브라우저 model-viewer 표시용).

USD/MDL 은 브라우저가 못 띄우지만 glTF/GLB 의 PBR(metallic-roughness)은 띄운다.
배정된 재질을 PBR 근사(베이스컬러+메탈릭+러프니스)로 변환해 GLB 로 내보낸다.
정밀한 MDL 룩은 Omniverse 전용 — 여기선 색/금속성/거칠기 근사."""

from __future__ import annotations

from typing import Any

import numpy as np

# 금속 계열 키워드(메탈릭=1 판정)
_METAL_KW = (
    "steel", "alumin", "metal", "copper", "brass", "bronze", "iron",
    "chrome", "gold", "silver", "stainless", "galvan", "aluminum",
)


def _is_metal(*words: str) -> bool:
    s = " ".join(w for w in words if w).lower()
    return any(k in s for k in _METAL_KW)


def _glb_from_parts(parts: list[tuple[str, np.ndarray, np.ndarray]], pbr_for) -> bytes:
    """parts=[(name,V,F)], pbr_for(name)->{baseColor:[r,g,b](0..1), metallic, roughness} -> GLB bytes."""
    import trimesh
    from trimesh.visual.material import PBRMaterial

    scene = trimesh.Scene()
    seen: dict[str, int] = {}
    for name, V, F in parts:
        spec = pbr_for(name) or {}
        col = spec.get("baseColor", [0.7, 0.7, 0.7])
        rgba = [int(max(0.0, min(1.0, c)) * 255) for c in col[:3]] + [255]
        mat = PBRMaterial(
            baseColorFactor=rgba,
            metallicFactor=float(spec.get("metallic", 0.0)),
            roughnessFactor=float(spec.get("roughness", 0.6)),
        )
        mesh = trimesh.Trimesh(vertices=np.asarray(V, dtype=np.float64), faces=np.asarray(F, dtype=np.int64))
        mesh.visual = trimesh.visual.TextureVisuals(material=mat)
        seen[name] = seen.get(name, 0) + 1
        node = name if seen[name] == 1 else f"{name}_{seen[name]}"
        scene.add_geometry(mesh, node_name=node)
    return scene.export(file_type="glb")


def glb_from_assignment_parts(parts, assignment: dict[str, Any]) -> bytes:
    """material-usd: 정규화된 parts + assignment -> PBR GLB. 부품 인덱스로 재질을 찾는다
    (이름이 중복돼도 부품별로 다른 재질이 적용되도록). 인덱스 없으면 이름→기본키로 폴백."""
    import trimesh
    from trimesh.visual.material import PBRMaterial

    parts_map = assignment.get("parts", {})
    palette = assignment.get("palette", {})

    scene = trimesh.Scene()
    seen: dict[str, int] = {}
    for i, (name, V, F) in enumerate(parts):
        key = parts_map.get(str(i)) or parts_map.get(name) or parts_map.get("__default__", "default")
        spec = palette.get(key, {})
        inp = spec.get("inputs", {})
        col = inp.get("paint_color") or inp.get("diffuse_color") or [0.7, 0.7, 0.7]
        rough = inp.get("paint_roughness")
        metal = _is_metal(spec.get("mdl", ""), spec.get("subId", ""), key)
        rgba = [int(max(0.0, min(1.0, c)) * 255) for c in list(col)[:3]] + [255]
        mat = PBRMaterial(
            baseColorFactor=rgba,
            metallicFactor=1.0 if metal else 0.0,
            roughnessFactor=float(rough) if rough is not None else (0.4 if metal else 0.7),
        )
        mesh = trimesh.Trimesh(vertices=np.asarray(V, dtype=np.float64), faces=np.asarray(F, dtype=np.int64))
        mesh.visual = trimesh.visual.TextureVisuals(material=mat)
        seen[name] = seen.get(name, 0) + 1
        node = name if seen[name] == 1 else f"{name}_{seen[name]}"
        scene.add_geometry(mesh, node_name=node)
    return scene.export(file_type="glb")


def glb_from_usd(usd_bytes: bytes) -> bytes:
    """재질 바인딩된 (자기완결) USD -> PBR GLB."""
    parts, pbr_for = usd_parts_and_pbr(usd_bytes)
    return _glb_from_parts(parts, pbr_for)


def glb_from_usd_with_bindings(usd_bytes: bytes, bindings: dict[str, str]) -> bytes:
    """입력 USD(자기완결 지오메트리) + (부품->재질이름) bindings -> PBR GLB.
    content-agents 출력은 입력을 서브레이어로 참조해 자기완결이 아니므로,
    입력 지오메트리에 추론된 재질 이름을 휴리스틱 PBR 로 입혀 미리보기."""
    parts = _load_usd_meshes(usd_bytes)

    def pbr_for(name: str) -> dict[str, Any]:
        return _heuristic_pbr(bindings.get(name, name))

    return _glb_from_parts(parts, pbr_for)


def _load_usd_meshes(usd_bytes: bytes) -> list[tuple[str, np.ndarray, np.ndarray]]:
    """USD 바이트 -> [(name, V(world,meters), F)]. 삼각분해 + 월드변환 + metersPerUnit."""
    import os
    import tempfile

    from pxr import Usd, UsdGeom

    fd, p = tempfile.mkstemp(suffix=".usd")
    os.close(fd)
    with open(p, "wb") as f:
        f.write(usd_bytes)
    parts: list[tuple[str, np.ndarray, np.ndarray]] = []
    try:
        stage = Usd.Stage.Open(p)
        mpu = UsdGeom.GetStageMetersPerUnit(stage) or 1.0
        xf = UsdGeom.XformCache()
        for prim in stage.Traverse():
            if not prim.IsA(UsdGeom.Mesh):
                continue
            m = UsdGeom.Mesh(prim)
            pts = m.GetPointsAttr().Get()
            counts = m.GetFaceVertexCountsAttr().Get()
            idx = m.GetFaceVertexIndicesAttr().Get()
            if not pts or not counts or not idx:
                continue
            V = np.asarray(pts, dtype=np.float64)
            M = np.array(xf.GetLocalToWorldTransform(prim), dtype=np.float64).reshape(4, 4)
            V = (np.c_[V, np.ones(len(V))] @ M)[:, :3] * mpu
            counts = np.asarray(counts, dtype=np.int64)
            idx = np.asarray(idx, dtype=np.int64)
            faces, o = [], 0
            for c in counts:
                for k in range(1, c - 1):
                    faces.append([idx[o], idx[o + k], idx[o + k + 1]])
                o += c
            parts.append((prim.GetName(), V, np.asarray(faces, dtype=np.int64)))
        return parts
    finally:
        try:
            os.remove(p)
        except OSError:
            pass


# ---------- material-usd 배정 -> PBR ----------
def pbr_from_assignment(assignment: dict[str, Any]):
    """material-usd assignment(part->key, palette[key]={mdl,subId,inputs}) -> pbr_for(name)."""
    parts_map = assignment.get("parts", {})
    palette = assignment.get("palette", {})

    def pbr_for(name: str) -> dict[str, Any]:
        key = parts_map.get(name, parts_map.get("__default__", "default"))
        spec = palette.get(key, {})
        inp = spec.get("inputs", {})
        col = inp.get("paint_color") or inp.get("diffuse_color") or [0.7, 0.7, 0.7]
        rough = inp.get("paint_roughness")
        metal = _is_metal(spec.get("mdl", ""), spec.get("subId", ""), key)
        return {
            "baseColor": list(col)[:3],
            "metallic": 1.0 if metal else 0.0,
            "roughness": float(rough) if rough is not None else (0.4 if metal else 0.7),
        }

    return pbr_for


# ---------- content-agents 출력 USD -> parts + PBR ----------
def usd_parts_and_pbr(usd_bytes: bytes):
    """재질 바인딩된 USD -> (parts[(name,V,F)], pbr_for(name)). 바인딩 머티리얼의
    UsdPreviewSurface 가 있으면 그 색/메탈릭/러프니스를, 없으면 이름 휴리스틱."""
    import os
    import tempfile

    from pxr import Usd, UsdGeom, UsdShade

    fd, p = tempfile.mkstemp(suffix=".usd")
    os.close(fd)
    with open(p, "wb") as f:
        f.write(usd_bytes)
    parts: list[tuple[str, np.ndarray, np.ndarray]] = []
    pbr_map: dict[str, dict[str, Any]] = {}
    try:
        stage = Usd.Stage.Open(p)
        mpu = UsdGeom.GetStageMetersPerUnit(stage) or 1.0
        xf = UsdGeom.XformCache()
        for prim in stage.Traverse():
            if not prim.IsA(UsdGeom.Mesh):
                continue
            m = UsdGeom.Mesh(prim)
            pts = m.GetPointsAttr().Get()
            counts = m.GetFaceVertexCountsAttr().Get()
            idx = m.GetFaceVertexIndicesAttr().Get()
            if not pts or not counts or not idx:
                continue
            V = np.asarray(pts, dtype=np.float64)
            M = np.array(xf.GetLocalToWorldTransform(prim), dtype=np.float64).reshape(4, 4)
            V = (np.c_[V, np.ones(len(V))] @ M)[:, :3] * mpu
            counts = np.asarray(counts, dtype=np.int64)
            idx = np.asarray(idx, dtype=np.int64)
            faces, o = [], 0
            for c in counts:
                for k in range(1, c - 1):
                    faces.append([idx[o], idx[o + k], idx[o + k + 1]])
                o += c
            name = prim.GetName()
            parts.append((name, V, np.asarray(faces, dtype=np.int64)))
            pbr_map[name] = _pbr_from_bound_material(prim, UsdShade)
        return parts, (lambda n: pbr_map.get(n))
    finally:
        try:
            os.remove(p)
        except OSError:
            pass


def _pbr_from_bound_material(prim, UsdShade) -> dict[str, Any]:
    """바인딩 머티리얼의 UsdPreviewSurface 입력에서 PBR 추출, 없으면 이름 휴리스틱."""
    mat_path = UsdShade.MaterialBindingAPI(prim).GetDirectBinding().GetMaterialPath()
    mat_name = str(mat_path).split("/")[-1] if mat_path else prim.GetName()
    # try UsdPreviewSurface
    try:
        from pxr import Sdf
        stage = prim.GetStage()
        mat_prim = stage.GetPrimAtPath(mat_path) if mat_path else None
        if mat_prim and mat_prim.IsValid():
            for shp in mat_prim.GetChildren():
                sh = UsdShade.Shader(shp)
                sid = sh.GetIdAttr().Get() if sh else None
                if sid == "UsdPreviewSurface":
                    def g(n, d):
                        a = sh.GetInput(n)
                        v = a.Get() if a else None
                        return v if v is not None else d
                    dc = g("diffuseColor", None)
                    base = list(dc)[:3] if dc is not None else None
                    metal = float(g("metallic", 0.0) or 0.0)
                    rough = float(g("roughness", 0.5) or 0.5)
                    if base is not None:
                        return {"baseColor": base, "metallic": metal, "roughness": rough}
    except Exception:
        pass
    return _heuristic_pbr(mat_name)


def _heuristic_pbr(name: str) -> dict[str, Any]:
    n = name.lower()
    if _is_metal(n):
        col = [0.86, 0.55, 0.30] if "copper" in n or "bronze" in n or "brass" in n else [0.79, 0.81, 0.84]
        return {"baseColor": col, "metallic": 1.0, "roughness": 0.35 if "brush" in n or "polish" in n else 0.5}
    if "rubber" in n:
        return {"baseColor": [0.05, 0.05, 0.05], "metallic": 0.0, "roughness": 0.9}
    if "concrete" in n or "stucco" in n or "plaster" in n:
        return {"baseColor": [0.62, 0.60, 0.56], "metallic": 0.0, "roughness": 0.95}
    if "ivory" in n or "cream" in n:
        return {"baseColor": [0.90, 0.87, 0.78], "metallic": 0.0, "roughness": 0.6}
    if "plastic" in n or "poly" in n:
        if "dark" in n or "black" in n:
            return {"baseColor": [0.12, 0.12, 0.13], "metallic": 0.0, "roughness": 0.5}
        return {"baseColor": [0.30, 0.35, 0.45], "metallic": 0.0, "roughness": 0.5}
    return {"baseColor": [0.7, 0.7, 0.7], "metallic": 0.0, "roughness": 0.6}
