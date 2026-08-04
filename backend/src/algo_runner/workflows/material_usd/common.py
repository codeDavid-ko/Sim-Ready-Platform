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
    """USD 입력: 메시별로 추출 — 월드변환 + metersPerUnit + upAxis 적용(→ meters, Z-up).

    중요: 예전엔 로컬 좌표만 읽어(배치 xform 무시) 부품이 전부 원점에 겹쳤고
    metersPerUnit 도 무시해 스케일이 어긋났다. USD 는 단위/업축을 자기서술하므로
    여기서 월드변환·mpu·upAxis 를 모두 반영해 meters·Z-up 으로 돌려준다.
    인스턴스 프록시(레퍼런스/instanceable)도 순회해 누락을 막는다.
    환경/헬퍼 지오메트리(purpose=guide/proxy, 거대 바닥 평면)는 제외한다.

    단위 sanity: 일부 익스포트는 부품마다 0.001 스케일 변환 + metersPerUnit=0.001 을
    이중으로 걸어 자산이 1000배 작아진다(부품이 점으로 붕괴). 작은 mpu 를 적용했을 때
    전체 자산이 비현실적으로 작아지면(<2cm) mpu 오기입으로 보고 1.0 을 쓴다.
    """
    try:
        from pxr import Usd, UsdGeom
    except ImportError:
        raise SystemExit("USD 입력에는 usd-core(pxr)가 필요합니다: pip install usd-core")
    stage = Usd.Stage.Open(path)
    mpu = UsdGeom.GetStageMetersPerUnit(stage) or 1.0
    up = (UsdGeom.GetStageUpAxis(stage) or "Y").upper()
    xf = UsdGeom.XformCache()
    pred = Usd.TraverseInstanceProxies(Usd.PrimDefaultPredicate)

    # 1차: 월드 좌표(스테이지 단위, mpu 미적용)로 수집 + 부품별 최대치수 기록
    raw = []  # (name, V_world_stageunits, F)
    dims = []  # 부품별 최대 치수(스테이지 단위)
    for prim in stage.Traverse(pred):
        if not prim.IsA(UsdGeom.Mesh):
            continue
        purpose = UsdGeom.Imageable(prim).ComputePurpose()
        if purpose in (UsdGeom.Tokens.guide, UsdGeom.Tokens.proxy):
            continue
        m = UsdGeom.Mesh(prim)
        pts = m.GetPointsAttr().Get()
        counts = m.GetFaceVertexCountsAttr().Get()
        idx = m.GetFaceVertexIndicesAttr().Get()
        if not pts or not counts or not idx:
            continue
        V = np.asarray(pts, float)
        M = np.array(xf.GetLocalToWorldTransform(prim), float).reshape(4, 4)
        V = (np.c_[V, np.ones(len(V))] @ M)[:, :3]  # 월드, 스테이지 단위(mpu 전)
        counts = np.asarray(counts, int)
        idx = np.asarray(idx, int)
        faces, o = [], 0
        for c in counts:  # 삼각형 팬 분해
            for k in range(1, c - 1):
                faces.append([idx[o], idx[o + k], idx[o + k + 1]])
            o += c
        raw.append((prim.GetName(), V, np.asarray(faces, int)))
        dims.append(float((V.max(0) - V.min(0)).max()))

    # 유효 mpu 결정: 작은 mpu 가 '대표(median) 부품'을 2cm 미만으로 축소하면 단위 오기입 → 1.0.
    # median 을 쓰는 이유: 거대 환경 평면(이상치) 하나가 최대값을 키워 판정을 흐리지 않게 함.
    eff = mpu
    rep = float(np.median(dims)) if dims else 0.0
    if mpu < 1.0 and rep >= 0.02 and rep * mpu < 0.02:
        eff = 1.0

    # 대표 부품 크기(meters) — 거대 평면 '상대 이상치' 판정 기준. median 이라 평면 1개에 안 흔들림.
    rep_m = float(np.median(dims)) * eff if dims else 0.0

    parts = []
    for name, V, F in raw:
        Vm = V * eff
        if up == "Y":  # USD Y-up → Z-up (meters 유지)
            Vm = np.column_stack([Vm[:, 0], -Vm[:, 2], Vm[:, 1]])
        # 거대 바닥/환경 평면 제외. 한 축이 사실상 0(평평)이면서 둘 중 하나:
        #   (a) 절대적으로 거대(>100m), 또는
        #   (b) 자산 대표 부품보다 압도적으로 큼(≥5m 이면서 median 의 8배 초과) — 상대 이상치.
        # (b)가 없으면 72m×12m 같은 '바닥'이 100m 미만이라 빠져나가 뷰어를 잡아먹는다.
        size_m = Vm.max(0) - Vm.min(0)
        mx, mn = float(size_m.max()), float(size_m.min())
        flat = mx > 0 and mn < mx * 1e-2
        huge_abs = mx > 100.0
        huge_rel = mx >= 5.0 and rep_m > 0 and mx > rep_m * 8.0
        if flat and (huge_abs or huge_rel):
            continue
        parts.append((name, Vm, F))
    return parts


def write_geometry_usd(parts, root_prim="Asset"):
    """parts(정규화된 mm·Z-up [(name,V,F)]) → 재질·평면 없는 맨 지오메트리 crate(.usd) 바이트.

    비교(compare)에서 두 엔진에 '같은 깨끗한 raw 지오메트리'를 주기 위함. 입력 USD의
    깨진 baked 재질·거대 평면·잘못된 단위는 로더 단계에서 이미 정리됐고, 여기선 그 결과를
    재질 없이 다시 자기완결 USD로 써서 NVIDIA content-agent(원래 raw→재질 추론 도구)에 준다.
    """
    import tempfile

    from pxr import Tf, Usd, UsdGeom, Vt

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, 0.001)  # parts 는 mm
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    root = UsdGeom.Xform.Define(stage, f"/{root_prim}")
    stage.SetDefaultPrim(root.GetPrim())
    seen: dict[str, int] = {}
    for name, V, F in parts:
        V = np.asarray(V, "float32")
        F = np.asarray(F).reshape(-1, 3)
        seen[name] = seen.get(name, 0) + 1
        nm = Tf.MakeValidIdentifier(name if seen[name] == 1 else f"{name}_{seen[name]}")
        m = UsdGeom.Mesh.Define(stage, f"/{root_prim}/{nm}")
        m.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(V))
        m.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(np.full(len(F), 3, dtype="int32")))
        m.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(F.reshape(-1).astype("int32")))
        m.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    fd, outp = tempfile.mkstemp(suffix=".usd")
    os.close(fd)
    try:
        stage.Export(outp)
        with open(outp, "rb") as f:
            return f.read()
    finally:
        try:
            os.remove(outp)
        except OSError:
            pass


def strip_env_planes(usd_bytes, max_m=100.0):
    """USD에서 거대 환경/바닥 평면(월드 크기 >max_m 이며 두께 0급)을 비활성화한 USD 바이트.

    비교(compare) 등에서 두 엔진에 '같은 깨끗한 자산'을 주기 위해 입력을 정리한다.
    (NVIDIA 멀티뷰 렌더가 72km 바닥 평면 때문에 빈 화면이 되는 문제 회피.)
    반환: (cleaned_bytes, removed_names). 제거 대상이 없으면 원본 그대로 돌려준다.
    """
    import tempfile

    from pxr import Usd, UsdGeom

    fd, p = tempfile.mkstemp(suffix=".usd")
    os.close(fd)
    with open(p, "wb") as f:
        f.write(usd_bytes)
    try:
        stage = Usd.Stage.Open(p)
        mpu = UsdGeom.GetStageMetersPerUnit(stage) or 1.0
        xf = UsdGeom.XformCache()
        removed = []
        for prim in stage.Traverse():  # 실편집 가능한 prim 만(인스턴스 프록시 제외)
            if not prim.IsA(UsdGeom.Mesh):
                continue
            pts = UsdGeom.Mesh(prim).GetPointsAttr().Get()
            if not pts:
                continue
            V = np.asarray(pts, float)
            M = np.array(xf.GetLocalToWorldTransform(prim), float).reshape(4, 4)
            V = (np.c_[V, np.ones(len(V))] @ M)[:, :3] * mpu
            size = V.max(0) - V.min(0)
            if float(size.max()) > max_m and float(size.min()) < float(size.max()) * 1e-3:
                prim.SetActive(False)  # 비활성 → 합성 스테이지/렌더에서 제외(비파괴)
                removed.append(prim.GetName())
        if not removed:
            return usd_bytes, []
        fd2, p2 = tempfile.mkstemp(suffix=".usd")
        os.close(fd2)
        try:
            stage.GetRootLayer().Export(p2)  # 루트 레이어만 → 구조·크기·인스턴싱 보존
            with open(p2, "rb") as f:
                data = f.read()
        finally:
            try:
                os.remove(p2)
            except OSError:
                pass
        return data, removed
    finally:
        try:
            os.remove(p)
        except OSError:
            pass


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


_LIGHT_BLOCK = (
    '\n    def DistantLight "DefaultLight"\n    {\n'
    "        float inputs:intensity = 3000\n"
    "        float inputs:angle = 0.53\n"
    "        color3f inputs:color = (1, 1, 1)\n"
    "        double3 xformOp:rotateXYZ = (-45, 0, 0)\n"
    '        uniform token[] xformOpOrder = ["xformOp:rotateXYZ"]\n'
    "    }\n"
)


def write_usd_string(parts, part_to_key, palette, root_prim="Asset", recenter=True, add_light=True):
    """parts(정규화됨) + (부품->재질키) + (재질키->vMaterials 사양) -> 자기완결 USDA 문자열.

    add_light=True 면 Isaac Sim 등에서 바로 보이도록 기본 DistantLight 를 포함한다
    (라이트 없는 씬 경고 방지). 에셋 라이브러리로 참조할 땐 False 로 끌 수 있다.
    """
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
        + (_LIGHT_BLOCK if add_light else "")
        + "\n}\n"
    )
    info = {
        "size_mm": [round(float(x), 1) for x in (mx - mn)],
        "meshes": len(meshes),
        "materials": list(used),
    }
    return body, info


def write_usd_crate(parts, part_to_key, palette, root_prim="Asset", recenter=True, add_light=True):
    """write_usd_string 의 바이너리 crate(.usd) 버전 — pxr API 로 직접 작성.

    정점/인덱스를 텍스트로 포맷하지 않고 numpy→Vt 배열로 바로 넣어, 수백만 정점 모델도
    빠르고 작게(텍스트 .usda 대비 ~10배↓) 내보낸다. vMaterials MDL 바인딩은 동일.
    반환: (crate_bytes, info)  — info 에 사람이 읽을 구조 요약 preview_text 포함.
    """
    import tempfile

    from pxr import Gf, Sdf, Tf, Usd, UsdGeom, UsdLux, UsdShade, Vt

    allV = np.vstack([V for _, V, _ in parts])
    mn, mx = allV.min(0), allV.max(0)
    cx, cy, bz = (mn[0] + mx[0]) / 2, (mn[1] + mx[1]) / 2, mn[2]
    shift = np.array([cx, cy, bz], dtype=float) if recenter else np.zeros(3)

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, 0.001)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    root = UsdGeom.Xform.Define(stage, f"/{root_prim}")
    stage.SetDefaultPrim(root.GetPrim())
    UsdGeom.Scope.Define(stage, f"/{root_prim}/Materials")

    # 부품 i 의 재질 키: 인덱스 우선(이름 중복 대비), 없으면 이름→기본키. 팔레트에 없으면 기본키.
    default_key = part_to_key.get("__default__", "default")
    if default_key not in palette:
        default_key = next(iter(palette))

    def _keyfor(i, name):
        k = part_to_key.get(str(i)) or part_to_key.get(name) or default_key
        return k if k in palette else default_key

    # 재질(MDL) — 사용된 키만 한 번씩
    mat_by_key: dict[str, "UsdShade.Material"] = {}
    used: dict[str, dict] = {}
    for i, (_name, _V, _F) in enumerate(parts):
        used.setdefault(_keyfor(i, _name), palette[_keyfor(i, _name)])
    for key, spec in used.items():
        mpath = f"/{root_prim}/Materials/{key}"
        mat = UsdShade.Material.Define(stage, mpath)
        sh = UsdShade.Shader.Define(stage, f"{mpath}/Shader")
        sh.CreateImplementationSourceAttr(UsdShade.Tokens.sourceAsset)
        rootmdl = spec["vmat_root"].rstrip("/")
        sh.SetSourceAsset(Sdf.AssetPath(f"{rootmdl}/{spec['mdl']}"), "mdl")
        sh.SetSourceAssetSubIdentifier(spec["subId"], "mdl")
        for k, v in spec.get("inputs", {}).items():
            if isinstance(v, (list, tuple)):
                sh.CreateInput(k, Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(float(v[0]), float(v[1]), float(v[2])))
            else:
                sh.CreateInput(k, Sdf.ValueTypeNames.Float).Set(float(v))
        out = sh.CreateOutput("out", Sdf.ValueTypeNames.Token)
        mat.CreateSurfaceOutput("mdl").ConnectToSource(out)
        mat_by_key[key] = mat

    # 메시 — Vt 배열 직결(텍스트 포맷 없음)
    seen: dict[str, int] = {}
    for i, (name, V, F) in enumerate(parts):
        V = (V - shift).astype("float32")
        F = np.asarray(F).reshape(-1, 3)
        seen[name] = seen.get(name, 0) + 1
        nm = Tf.MakeValidIdentifier(name if seen[name] == 1 else f"{name}_{seen[name]}")
        m = UsdGeom.Mesh.Define(stage, f"/{root_prim}/{nm}")
        m.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(V))
        m.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(np.full(len(F), 3, dtype="int32")))
        m.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(F.reshape(-1).astype("int32")))
        m.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
        key = _keyfor(i, name)
        UsdShade.MaterialBindingAPI(m.GetPrim()).Bind(mat_by_key[key])

    if add_light:
        dl = UsdLux.DistantLight.Define(stage, f"/{root_prim}/DefaultLight")
        dl.CreateIntensityAttr(3000.0)
        dl.CreateAngleAttr(0.53)
        dl.CreateColorAttr(Gf.Vec3f(1, 1, 1))
        UsdGeom.XformCommonAPI(dl.GetPrim()).SetRotate(Gf.Vec3f(-45, 0, 0))

    fd, outp = tempfile.mkstemp(suffix=".usd")
    os.close(fd)
    try:
        stage.Export(outp)
        with open(outp, "rb") as f:
            data = f.read()
    finally:
        try:
            os.remove(outp)
        except OSError:
            pass

    nmesh = len(parts)
    nvtx = int(sum(len(V) for _, V, _ in parts))
    preview = (
        f"#usd crate (binary)  root=/{root_prim}  metersPerUnit=0.001  upAxis=Z\n"
        f"meshes: {nmesh}  vertices: {nvtx:,}  materials: {len(used)}\n"
        f"materials: {', '.join(used)}\n"
        f"size_mm: {[round(float(x), 1) for x in (mx - mn)]}\n"
        f"(바이너리 crate — 텍스트 미리보기 대신 구조 요약. Isaac/Omniverse 에서 바로 열림)"
    )
    info = {
        "size_mm": [round(float(x), 1) for x in (mx - mn)],
        "meshes": nmesh,
        "materials": list(used),
        "vertices": nvtx,
        "preview_text": preview,
    }
    return data, info
