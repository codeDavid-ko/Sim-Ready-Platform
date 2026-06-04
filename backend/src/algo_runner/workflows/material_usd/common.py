"""common.py — 공용 함수 (형상 로드 / 정규화 / USD 작성).

material-usd-workflow 의 검증된 레퍼런스 로직을 플랫폼 패키지로 가져온 것.
핵심 교훈(GIS_외형도.STEP 작업에서 검증):
  - STEP은 부품(로컬좌표)+배치(씬그래프). 부품별 처리 시 graph 변환을 정점에 적용.
  - SolidWorks STEP은 보통 Y-up, cascadio 경유 시 meter → Z-up·mm 변환.
  - 출력 USD는 외부참조 없는 자기완결(단일파일 이동만으로 열림).
의존: trimesh, cascadio(STEP), numpy. (USD 입력은 pxr 선택)
"""

from __future__ import annotations

import os

import numpy as np


def load_parts(path):
    """STEP/STL/USD -> [(name, V(Nx3, 원본단위/좌표), F(Mx3))] (배치변환 적용)."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".usd", ".usda", ".usdc", ".usdz"):
        return _load_usd_parts(path)
    import trimesh

    scene = trimesh.load(path)
    parts = []
    if isinstance(scene, trimesh.Scene):
        for node in scene.graph.nodes_geometry:  # 부품별 + 배치변환
            T, gname = scene.graph[node]
            geo = scene.geometry[gname]
            V = trimesh.transformations.transform_points(geo.vertices, T)
            parts.append((gname, np.asarray(V, float), np.asarray(geo.faces, int)))
    else:
        parts.append(("part", np.asarray(scene.vertices, float), np.asarray(scene.faces, int)))
    return parts


def _load_usd_parts(path):
    """USD 입력: pxr가 있으면 메시별로 추출."""
    try:
        from pxr import Usd, UsdGeom
    except ImportError:
        raise SystemExit("USD 입력에는 usd-core(pxr)가 필요합니다: pip install usd-core")
    stage = Usd.Stage.Open(path)
    parts = []
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Mesh):
            m = UsdGeom.Mesh(prim)
            pts = np.asarray(m.GetPointsAttr().Get(), float)
            counts = np.asarray(m.GetFaceVertexCountsAttr().Get(), int)
            idx = np.asarray(m.GetFaceVertexIndicesAttr().Get(), int)
            faces, o = [], 0
            for c in counts:  # 삼각형 팬 분해
                for k in range(1, c - 1):
                    faces.append([idx[o], idx[o + k], idx[o + k + 1]])
                o += c
            parts.append((prim.GetName(), pts, np.asarray(faces, int)))
    return parts


def normalize(V, in_units="m", up_axis="Y"):
    """원본 정점 -> mm, Z-up."""
    V = np.asarray(V, float) * {"m": 1000.0, "mm": 1.0, "cm": 10.0}[in_units]
    if up_axis.upper() == "Y":
        V = np.column_stack([V[:, 0], -V[:, 2], V[:, 1]])
    return V


def part_summary(parts):
    """LLM/뷰어용 부품 메타데이터 (메시 데이터는 제외)."""
    out = []
    for name, V, F in parts:
        mn, mx = V.min(0), V.max(0)
        out.append(
            {
                "name": name,
                "bbox_min": [round(float(x), 1) for x in mn],
                "bbox_max": [round(float(x), 1) for x in mx],
                "size_mm": [round(float(x), 1) for x in (mx - mn)],
                "centroid": [round(float(x), 1) for x in (mn + mx) / 2],
                "vertex_count": int(len(V)),
            }
        )
    return out


# ---------- USD 작성 (자기완결) ----------
def _mat_block(key, spec, root_prim="Asset"):
    root = spec["vmat_root"].rstrip("/")
    lines = []
    for k, v in spec.get("inputs", {}).items():
        if isinstance(v, (list, tuple)):
            lines.append(f"                color3f inputs:{k} = ({v[0]}, {v[1]}, {v[2]})")
        else:
            lines.append(f"                float inputs:{k} = {v}")
    inp = "\n".join(lines)
    return f'''        def Material "{key}"
        {{
            token outputs:mdl:surface.connect = </{root_prim}/Materials/{key}/Shader.outputs:out>
            def Shader "Shader"
            {{
                uniform token info:implementationSource = "sourceAsset"
                uniform asset info:mdl:sourceAsset = @{root}/{spec["mdl"]}@
                uniform token info:mdl:sourceAsset:subIdentifier = "{spec["subId"]}"
{inp}
                token outputs:out
            }}
        }}'''


def _mesh_block(name, V, F, key, root_prim="Asset"):
    pts = ", ".join(f"({x:.2f}, {y:.2f}, {z:.2f})" for x, y, z in V)
    cnt = ", ".join(["3"] * len(F))
    idx = ", ".join(map(str, F.reshape(-1)))
    return f'''    def Mesh "{name}"
    {{
        int[] faceVertexCounts = [{cnt}]
        int[] faceVertexIndices = [{idx}]
        point3f[] points = [{pts}]
        uniform token subdivisionScheme = "none"
        rel material:binding = </{root_prim}/Materials/{key}>
    }}'''


def write_usd_string(parts, part_to_key, palette, root_prim="Asset", recenter=True):
    """parts(정규화됨) + (부품->재질키) + (재질키->vMaterials 사양) -> 자기완결 USDA 문자열."""
    allV = np.vstack([V for _, V, _ in parts])
    mn, mx = allV.min(0), allV.max(0)
    cx, cy, bz = (mn[0] + mx[0]) / 2, (mn[1] + mx[1]) / 2, mn[2]
    used, meshes, seen = {}, [], {}
    for name, V, F in parts:
        V = V.copy()
        if recenter:
            V[:, 0] -= cx
            V[:, 1] -= cy
            V[:, 2] -= bz
        key = part_to_key.get(name, part_to_key.get("__default__", "default"))
        used[key] = palette[key]
        seen[name] = seen.get(name, 0) + 1
        nm = name if seen[name] == 1 else f"{name}_{seen[name]}"
        meshes.append(_mesh_block(nm, V, F, key, root_prim))
    mats = "\n".join(_mat_block(k, s, root_prim) for k, s in used.items())
    body = (
        f'#usda 1.0\n(\n    defaultPrim = "{root_prim}"\n'
        f'    metersPerUnit = 0.001\n    upAxis = "Z"\n)\n\n'
        f'def Xform "{root_prim}"\n{{\n    def Scope "Materials"\n    {{\n{mats}\n    }}\n'
        + "\n".join(meshes)
        + "\n}\n"
    )
    info = {
        "size_mm": [round(float(x), 1) for x in (mx - mn)],
        "meshes": len(meshes),
        "materials": list(used),
    }
    return body, info
