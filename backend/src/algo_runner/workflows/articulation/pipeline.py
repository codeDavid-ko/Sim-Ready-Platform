"""articulation 파이프라인 — STEP/USD → 계층(트리) + 메시 → 관절(UsdPhysics) USD.

USD 의 실제 prim 계층(Xform/Mesh 트리)을 추출해 프런트에 주고, 사용자가 트리에서 고른
노드(서브트리)를 하나의 바디로 묶어 그 사이에 조인트를 건다. 메시는 월드좌표(미터)로
보내 프런트 Three.js 와 백엔드가 같은 좌표계를 쓴다(피벗/축 일치).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import PurePath
from typing import Any

import numpy as np

_USD_EXT = {".usd", ".usda", ".usdc", ".usdz"}
_STEP_EXT = {".step", ".stp"}
_SUPPORTED = _USD_EXT | _STEP_EXT

# Drive(모터) 게인 — 문서 §6/§8.5. 1e9/1e7 은 mm스케일용 과대값(슬램) → 완화. 위치드라이브+초기 target=0(닫힘).
_DRIVE_STIFF = {"angular": 1.0e5, "linear": 1.0e4}
_DRIVE_DAMP = {"angular": 1.0e4, "linear": 1.0e3}


def _quatf_inverse_of_world_rot(world_xform, Gf):
    """월드 변환의 회전 역(inverse) 을 GfQuatf 로. 조인트 로컬프레임을 '월드축'에 맞추는 데 씀."""
    try:
        q = world_xform.ExtractRotationQuat().GetNormalized().GetInverse()
        im = q.GetImaginary()
        return Gf.Quatf(float(q.GetReal()), float(im[0]), float(im[1]), float(im[2]))
    except Exception:  # noqa: BLE001
        return Gf.Quatf(1.0, 0.0, 0.0, 0.0)


def _apply_mass(prim, UsdPhysics):
    """질량/관성 보장 — 없으면 density 만 주어 PhysX 가 충돌형상에서 계산(0질량 폭발 방지)."""
    if not (prim and prim.IsValid()):
        return
    m = UsdPhysics.MassAPI.Apply(prim)
    try:
        if not m.GetMassAttr().HasAuthoredValue() and not m.GetDensityAttr().HasAuthoredValue():
            m.CreateDensityAttr(1000.0)
    except Exception:  # noqa: BLE001
        pass


def _apply_drive(J, jtype, lo, UsdPhysics):
    """revolute→angular / prismatic→linear 위치드라이브(문서 §6b). **초기 target=0(닫힘)** → Play 즉시 슬램 방지.
    인스펙터 슬라이더가 target 을 바꾸면 문이 따라옴. lo 는 무시(닫힘=0 기준)."""
    tok = "angular" if jtype == "revolute" else ("linear" if jtype == "prismatic" else None)
    if not tok or not hasattr(UsdPhysics, "DriveAPI"):
        return
    try:
        d = UsdPhysics.DriveAPI.Apply(J.GetPrim(), tok)
        d.CreateTypeAttr("force")
        d.CreateStiffnessAttr(float(_DRIVE_STIFF[tok]))
        d.CreateDampingAttr(float(_DRIVE_DAMP[tok]))
        d.CreateTargetPositionAttr(0.0)   # 닫힘 — 열린값으로 두면 Play 즉시 슬램(문서 §8)
    except Exception:  # noqa: BLE001
        pass


def _collision_approx_token(mesh_prim) -> str:
    """메시 오목도로 충돌 근사 자동 선택: 볼록→convexHull(싸고 정확), 오목→convexDecomposition(빈공간 보존).

    convexHull 은 속 빈 박스/컨테이너를 '꽉 찬 덩어리'로 만들어 안에 든 부품이 파고들어 Play 시
    폭발하듯 튕겨나간다. scipy(정확한 convex hull)가 없어 OBB(주축 정렬 바운딩) 채움률을 오목도
    프록시로 쓴다: 부품이 자기 OBB 를 거의 채우면(≥0.7) 볼록으로 보고 convexHull, 아니면 오목으로
    보고 convexDecomposition. 불확실하면 안전하게 오목(정확 충돌)으로 처리한다."""
    from pxr import UsdGeom, UsdPhysics
    try:
        import trimesh

        m = UsdGeom.Mesh(mesh_prim)
        P = m.GetPointsAttr().Get()
        C = m.GetFaceVertexCountsAttr().Get()
        I = m.GetFaceVertexIndicesAttr().Get()
        if not P or not C or not I:
            return UsdPhysics.Tokens.convexHull
        P = np.asarray(P, float).reshape(-1, 3)
        faces: list[list[int]] = []
        o = 0
        for c in C:
            for k in range(1, c - 1):
                faces.append([I[o], I[o + k], I[o + k + 1]])
            o += c
        tm = trimesh.Trimesh(P, np.asarray(faces, int), process=False)
        if bool(getattr(tm, "is_convex", False)):
            return UsdPhysics.Tokens.convexHull
        Q = P - P.mean(0)                                   # OBB: 공분산 주축에 정렬한 박스
        _w, R = np.linalg.eigh(np.cov(Q.T))
        ext = np.ptp(Q @ R, axis=0)                          # np.ptp (NumPy2: ndarray.ptp 제거됨)
        obbv = float(np.prod(ext))
        fill = abs(float(tm.volume)) / obbv if obbv > 1e-12 else 0.0
        return UsdPhysics.Tokens.convexHull if fill >= 0.7 else UsdPhysics.Tokens.convexDecomposition
    except Exception:  # noqa: BLE001
        return UsdPhysics.Tokens.convexDecomposition       # 불확실 → 안전하게 오목 취급


def _auto_collisions(stage, UsdGeom, UsdPhysics, Usd) -> tuple[int, int]:
    """충돌 메시마다 오목도 자동 판별로 근사 토큰을 재설정. (볼록수, 오목수) 반환."""
    n_cvx = n_dec = 0
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Mesh) and prim.HasAPI(UsdPhysics.CollisionAPI):
            tok = _collision_approx_token(prim)
            UsdPhysics.MeshCollisionAPI.Apply(prim).CreateApproximationAttr(tok)
            if tok == UsdPhysics.Tokens.convexDecomposition:
                n_dec += 1
            else:
                n_cvx += 1
    return n_cvx, n_dec


def _safe(s: str) -> str:
    r = "".join(c if c.isalnum() else "_" for c in str(s)).strip("_") or "node"
    if r[0].isdigit():        # USD prim 이름은 숫자로 시작 불가
        r = "n_" + r
    return r


# 프런트 미리보기/피킹용 메시 예산. 이 정점 수를 넘으면 각 메시를 bbox 박스로 근사해 응답을 가볍게 한다.
# (관절 정의/출력은 build 가 원본 파일을 다시 읽으므로 fidelity 영향 없음.)
_PREVIEW_VERT_BUDGET = 800_000

_BOX_FACES = [0, 1, 2, 0, 2, 3, 4, 6, 5, 4, 7, 6, 0, 4, 5, 0, 5, 1,
              1, 5, 6, 1, 6, 2, 2, 6, 7, 2, 7, 3, 3, 7, 4, 3, 4, 0]


def _box_verts(lo, hi):
    return [[lo[0], lo[1], lo[2]], [hi[0], lo[1], lo[2]], [hi[0], hi[1], lo[2]], [lo[0], hi[1], lo[2]],
            [lo[0], lo[1], hi[2]], [hi[0], lo[1], hi[2]], [hi[0], hi[1], hi[2]], [lo[0], hi[1], hi[2]]]


def _triangulate(counts, idx):
    """faceVertexCounts/Indices -> (m,3) int triangle array (fan)."""
    tris, o = [], 0
    for c in counts:
        for k in range(1, c - 1):
            tris.append((idx[o], idx[o + k], idx[o + k + 1]))
        o += c
    return np.asarray(tris, dtype=np.int64) if tris else np.zeros((0, 3), np.int64)


def _decimate(P, tris, keep):
    """그리드 정점 클러스터링으로 실제 형상을 유지하며 정점을 줄인다(numpy 전용, 외부 의존 없음).
    P: (n,3) local points, tris: (m,3), keep: 목표 유지 비율(0~1). -> (verts, tris) 감축본."""
    n = len(P)
    if keep >= 1.0 or n <= 200 or len(tris) == 0:
        return P, tris
    target = max(80, int(n * keep))
    lo, hi = P.min(0), P.max(0)
    ext = hi - lo; ext[ext <= 0] = 1e-9
    cells = max(2, int(round(target ** (1.0 / 3.0))))          # 축당 셀 수
    keys = np.floor((P - lo) / (ext / cells)).astype(np.int64)  # 각 정점 → 셀 인덱스
    uniq, inv = np.unique(keys, axis=0, return_inverse=True)     # 셀별 클러스터(해시충돌 없음)
    newP = np.zeros((len(uniq), 3)); cnt = np.zeros(len(uniq))
    np.add.at(newP, inv, P); np.add.at(cnt, inv, 1); newP /= cnt[:, None]  # 클러스터 중심
    nt = inv[tris]                                               # 삼각형 정점 재매핑
    good = (nt[:, 0] != nt[:, 1]) & (nt[:, 1] != nt[:, 2]) & (nt[:, 0] != nt[:, 2])
    return newP, nt[good]


# ───────────────────────── 추출 (트리 + 월드 메시) ─────────────────────────
def _extract_usd(path: str) -> tuple[list[dict], list[dict], str | None]:
    from pxr import Gf, Usd, UsdGeom

    stage = Usd.Stage.Open(path)
    mpu = UsdGeom.GetStageMetersPerUnit(stage) or 1.0
    xcache = UsdGeom.XformCache()
    mesh_prims: list[Any] = []

    def node(prim) -> dict:
        if prim.IsA(UsdGeom.Mesh):
            mesh_prims.append(prim)
        return {"path": str(prim.GetPath()), "name": prim.GetName() or "/",
                "type": str(prim.GetTypeName()) or "Xform",
                "children": [node(c) for c in prim.GetChildren()]}

    root = stage.GetDefaultPrim() or stage.GetPseudoRoot()
    tree = [node(c) for c in root.GetChildren()] if not root.IsA(UsdGeom.Mesh) else [node(root)]

    # 1) 원시 지오메트리 수집 + 총 정점 수 → 예산 초과면 프록시(박스) 모드.
    raw = []
    total = 0
    for prim in mesh_prims:
        m = UsdGeom.Mesh(prim)
        pts = m.GetPointsAttr().Get()
        counts = m.GetFaceVertexCountsAttr().Get()
        idx = m.GetFaceVertexIndicesAttr().Get()
        if not pts or not counts or not idx:
            continue
        raw.append((prim, pts, counts, idx))
        total += len(pts)
    proxy = total > _PREVIEW_VERT_BUDGET
    keep = min(1.0, _PREVIEW_VERT_BUDGET / total) if (proxy and total) else 1.0  # 데시메이트 목표 비율

    # 메시별 dict + 월드 bbox 수집(환경평면 판별/프레이밍용)
    built: list[tuple[dict, np.ndarray, np.ndarray]] = []
    for prim, pts, counts, idx in raw:
        M = xcache.GetLocalToWorldTransform(prim)
        if proxy:
            # 실제 형상을 그리드 클러스터링으로 데시메이트(박스 근사 대신) → 관절 정의 가능한 형상 유지
            P = np.asarray(pts, dtype=np.float64).reshape(-1, 3)
            dP, dT = _decimate(P, _triangulate(counts, idx), keep)
            if len(dP) == 0 or len(dT) == 0:      # 데시메이트로 다 사라지면 박스로 폴백
                llo, lhi = P.min(0), P.max(0)
                corners = [(x, y, z) for x in (llo[0], lhi[0]) for y in (llo[1], lhi[1]) for z in (llo[2], lhi[2])]
                W = np.array([[*M.Transform(Gf.Vec3d(float(c[0]), float(c[1]), float(c[2])))] for c in corners], float) * mpu
                wlo, whi = W.min(0), W.max(0)
                V = _box_verts(wlo, whi)
                d = {"path": str(prim.GetPath()), "name": prim.GetName(),
                     "vertices": [round(float(x), 5) for row in V for x in row], "faces": list(_BOX_FACES)}
            else:
                Vw = np.array([[*M.Transform(Gf.Vec3d(float(p[0]), float(p[1]), float(p[2])))] for p in dP], float) * mpu
                wlo, whi = Vw.min(0), Vw.max(0)
                d = {"path": str(prim.GetPath()), "name": prim.GetName(),
                     "vertices": [round(float(x), 5) for x in Vw.reshape(-1).tolist()],
                     "faces": [int(i) for i in dT.reshape(-1).tolist()]}
        else:
            Vw = np.array([[*M.Transform(Gf.Vec3d(p[0], p[1], p[2]))] for p in pts], float) * mpu
            wlo, whi = Vw.min(0), Vw.max(0)
            faces, o = [], 0
            for c in counts:
                for k in range(1, c - 1):
                    faces.append([idx[o], idx[o + k], idx[o + k + 1]])
                o += c
            d = {"path": str(prim.GetPath()), "name": prim.GetName(),
                 "vertices": [round(float(x), 5) for x in Vw.reshape(-1).tolist()],
                 "faces": [int(i) for i in np.asarray(faces, int).reshape(-1).tolist()]}
        built.append((d, wlo, whi))

    # 거대 환경/바닥 평면 제외(미리보기 프레이밍 정상화) — 납작 + 실제 객체 중앙값의 3배↑.
    # (트리에는 남아있어 필요시 선택 가능. build 출력은 원본 그대로라 영향 없음.)
    def _diag(wlo, whi):
        d = whi - wlo
        return float(np.linalg.norm(d)), float(np.max(np.abs(d))), float(np.min(np.abs(d)))
    stats = [(_diag(lo, hi)) for _d, lo, hi in built]
    nonflat = sorted(dg for dg, mx, mn in stats if mx > 0 and mn > mx * 0.02)
    alld = sorted(dg for dg, _mx, _mn in stats)
    ref = (nonflat[len(nonflat) // 2] if nonflat else (alld[len(alld) // 2] if alld else 0.0))
    meshes, stripped = [], 0
    for (d, _lo, _hi), (dg, mx, mn) in zip(built, stats):
        flat = mx > 0 and mn <= mx * 0.02
        huge = mx > 100.0 or (ref > 0 and dg > ref * 3.0)
        if flat and huge:
            stripped += 1
            continue
        meshes.append(d)
    if not meshes:           # 전부 제외되는 비정상 케이스 방어 → 원래대로
        meshes = [d for d, _l, _h in built]; stripped = 0

    parts = []
    if proxy:
        parts.append(f"대형 자산(메시 {len(built)}개)이라 3D 미리보기는 **형상을 간략화(데시메이트)**해 표시합니다.")
    if stripped:
        parts.append(f"거대 환경/바닥 평면 {stripped}개를 미리보기에서 제외했습니다(객체가 작게 보이던 문제).")
    if parts:
        parts.append("부품 선택·관절 정의·출력 USD 는 원본 형상 그대로입니다.")
    note = " ".join(parts) if parts else None
    return tree, meshes, note


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


def _unresolved_dep_hint(path: str) -> str | None:
    """업로드된 USD가 (함께 오지 않은) 외부 파일을 참조해 형상이 비어버린 경우를 감지해 안내 메시지를 만든다.
    sublayer/reference/payload 의 asset 경로 중 해석 안 되는 게 있으면 평탄화/usdz 를 권한다."""
    try:
        from pxr import UsdUtils
        _layers, _assets, unresolved = UsdUtils.ComputeAllDependencies(path)
        if unresolved:
            names = ", ".join(sorted({os.path.basename(str(u)) for u in unresolved})[:3])
            return ("이 USD는 외부 파일(" + names + " 등)을 참조하는데 업로드 시 그 파일이 함께 오지 않아 "
                    "형상을 찾지 못했습니다. Isaac/USD에서 '평탄화(Flatten)'한 단독 USD 또는 형상이 포함된 "
                    ".usdz 로 내보내 올려주세요.")
    except Exception:  # noqa: BLE001 — 진단 실패는 무시하고 일반 메시지로
        return None
    return None


def extract(file_bytes: bytes, name: str) -> tuple[list[dict], list[dict], str | None]:
    ext = PurePath(name).suffix.lower()
    if ext not in _SUPPORTED:
        raise ValueError(f"STEP 또는 USD만 지원합니다. 받은: {ext or '?'}")
    fd, path = tempfile.mkstemp(suffix=ext)
    with os.fdopen(fd, "wb") as f:
        f.write(file_bytes)
    up_axis = "Z"
    dep_hint = None
    try:
        if ext in _USD_EXT:
            tree, meshes, note = _extract_usd(path)
            try:
                from pxr import Usd, UsdGeom
                up_axis = "Y" if str(UsdGeom.GetStageUpAxis(Usd.Stage.Open(path))) == "Y" else "Z"
            except Exception:  # noqa: BLE001
                up_axis = "Z"
            if not meshes:
                dep_hint = _unresolved_dep_hint(path)   # temp 파일 살아있을 때 진단
        else:
            tree, meshes = _extract_step(path)
            note = None
            up_axis = "Z"   # STEP/CAD 관례상 Z-up
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    if not meshes:
        raise ValueError(dep_hint or "형상에서 메시를 찾지 못했습니다.")
    return tree, meshes, note, up_axis


# ───────────────────────── 관절 USD 저작 ─────────────────────────
def _common_prim_ancestor(stage, paths: list[str]):
    """여러 prim 경로의 공통 조상 prim(없으면 None). ArticulationRoot 부착 위치 결정용."""
    paths = [p for p in paths if p]
    if not paths:
        return None
    segs = [p.strip("/").split("/") for p in paths]
    common: list[str] = []
    for i in range(min(len(s) for s in segs)):
        col = {s[i] for s in segs}
        if len(col) == 1:
            common.append(col.pop())
        else:
            break
    if not common:
        return None
    pr = stage.GetPrimAtPath("/" + "/".join(common))
    return pr if (pr and pr.IsValid()) else None


def _under(mesh_path: str, node_path: str) -> bool:
    return mesh_path == node_path or mesh_path.startswith(node_path.rstrip("/") + "/")


def _finalize_self_contained(outp_usd: str) -> tuple[bytes, str]:
    """관절 출력(.usd)이 외부 자산(입력 usdz 내부 MDL/텍스처 등)을 참조하면 — 그대로 두면 입력 temp 삭제 후
    경로가 깨진다 — 입력 temp 가 아직 살아있는 지금 .usdz 로 묶어 자기완결로 만든다.
    외부 의존이 없으면(예: STEP 평면 출력) .usd 그대로. 반환 (bytes, ext)."""
    from pxr import Sdf, UsdUtils

    try:
        _layers, assets, unresolved = UsdUtils.ComputeAllDependencies(Sdf.AssetPath(outp_usd))
    except Exception:  # noqa: BLE001
        assets, unresolved = [], []
    if not list(assets) and not list(unresolved or []):
        with open(outp_usd, "rb") as f:
            return f.read(), ".usd"
    # 평탄화: 의존(MDL/텍스처)을 ./materials·./textures 로 풀고 경로 재앵커한 뒤 묶는다.
    # (CreateNewUsdzPackage 만 쓰면 입력 usdz 를 통째로 '중첩'(usdz 안 usdz)시켜 MDL 경로가
    #  tmp.usdz[materials/X.mdl] 가 되고 Isaac 이 셰이더를 못 찾아 룩이 깨진다 — 룩 합치기의
    #  _resolve_deps 로 평탄 ./materials 로 풀면 셰이더가 정상 resolve 된다.)
    import shutil
    work = tempfile.mkdtemp(prefix="art_finalize_")
    flat = os.path.join(work, "asset.usd")
    shutil.copyfile(outp_usd, flat)
    try:
        from ..look_merge.pipeline import _resolve_deps
        _resolve_deps(flat, work, [], fetch_remote=True)
    except Exception:  # noqa: BLE001 — 평탄화 실패 시 원본 .usd 로 폴백
        flat = outp_usd
    outz = os.path.join(work, "asset.usdz")
    try:
        UsdUtils.CreateNewUsdzPackage(Sdf.AssetPath(flat), outz)
        with open(outz, "rb") as f:
            return f.read(), ".usdz"
    except Exception:  # noqa: BLE001 — 온라인 MDL 등으로 묶기 실패하면 .usd 폴백(룩 합치기 카드로 자기완결화 가능)
        with open(outp_usd, "rb") as f:
            return f.read(), ".usd"


def author_preserve(file_bytes: bytes, name: str, joints: list[dict[str, Any]], drive: bool = True, weld_loose: bool = True, loose_mode: str = "weld") -> tuple[bytes, list[dict[str, Any]], list[str], str]:
    """**원본 계층을 보존**한 채 선택 prim 에 RigidBody/Collision + UsdPhysics 조인트만 얹는다.
    구조(트리)를 그대로 유지하고 물리만 추가 → 메시/객체 구조가 깨지지 않는다.

    pivot 은 프런트가 보낸 월드 미터좌표 → 스테이지 단위(/mpu)로 변환 후 각 바디 로컬프레임으로.
    drive=True 면 revolute/prismatic 에 위치 드라이브(모터)를 단다. SimReady Prop 납품엔 모터가
    필수가 아니므로(drive 는 Robot-Body 프로파일 전용) drive=False 로 모터 없이 순수 관절만 낼 수 있다."""
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

    ext = PurePath(name).suffix.lower()
    # STEP 입력은 반드시 원본 확장자를 유지해야 trimesh 가 STEP 로더로 읽는다.
    # (이전엔 STEP 을 .usd 로 써서 _extract_step 의 trimesh.load 가 'usd' 로 오인 → 크래시)
    fd, path = tempfile.mkstemp(suffix=ext if ext in _SUPPORTED else ".usd")
    with os.fdopen(fd, "wb") as f:
        f.write(file_bytes)
    fd2, outp = tempfile.mkstemp(suffix=".usd")
    os.close(fd2)
    _usdz_ctx = None   # usdz 입력 시 (workdir, arcnames, usd_arc) — 원본 패키지 보존 재패키징용
    try:
        if ext == ".usdz":
            import zipfile
            _wd = tempfile.mkdtemp(prefix="art_usdz_in_")
            _zin = zipfile.ZipFile(path); _arcs = _zin.namelist(); _zin.extractall(_wd); _zin.close()
            _usd_arc = next((n for n in _arcs if n.lower().endswith((".usd", ".usdc", ".usda"))), None)
            if _usd_arc:
                path = os.path.join(_wd, _usd_arc)   # loose .usd 를 열어 MDL/텍스처가 원본 상대경로로 해석됨
                _usdz_ctx = (_wd, _arcs, _usd_arc)
        if ext in _STEP_EXT:
            # STEP 은 계층이 없으므로 평면 USD 로 변환 후 보존 경로 사용(자기완결 .usd — 외부 MDL 없음)
            tree, meshes = _extract_step(path)
            d, a, n = _author_flat(meshes, joints, drive=drive)
            return d, a, n, ".usd"

        stage = Usd.Stage.Open(path)
        mpu = UsdGeom.GetStageMetersPerUnit(stage) or 1.0
        xcache = UsdGeom.XformCache()
        bcache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
        up_tok = str(UsdGeom.GetStageUpAxis(stage))
        up_axis = "Y" if up_tok == "Y" else "Z"   # 회전축 기본값 = 스테이지 up-axis(문서 §1)

        # PhysicsScene (없으면 추가)
        if not any(p.IsA(UsdPhysics.Scene) for p in stage.Traverse()):
            sc = UsdPhysics.Scene.Define(stage, "/PhysicsScene")
            sc.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1)); sc.CreateGravityMagnitudeAttr(9.81)

        parents_set: set[str] = set()
        children_set: set[str] = set()

        def apply_body(prim_path: str) -> None:
            prim = stage.GetPrimAtPath(prim_path)
            if not prim or not prim.IsValid():
                return
            UsdPhysics.RigidBodyAPI.Apply(prim)
            for d in Usd.PrimRange(prim):
                if d.IsA(UsdGeom.Mesh):
                    UsdPhysics.CollisionAPI.Apply(d)
                    UsdPhysics.MeshCollisionAPI.Apply(d).CreateApproximationAttr(UsdPhysics.Tokens.convexHull)

        def piv_world(j, child_prim) -> Gf.Vec3d:
            if j.get("pivot"):
                p = j["pivot"]
                return Gf.Vec3d(float(p[0]) / mpu, float(p[1]) / mpu, float(p[2]) / mpu)
            rng = bcache.ComputeWorldBound(child_prim).ComputeAlignedRange()
            c = (rng.GetMin() + rng.GetMax()) * 0.5
            return Gf.Vec3d(c[0], c[1], c[2])

        # ── 1) 유효 조인트 수집 + 바디 집합 ──
        vjoints = []
        for j in joints:
            cp = j.get("child")
            if cp and stage.GetPrimAtPath(cp) and stage.GetPrimAtPath(cp).IsValid():
                vjoints.append(j)
                children_set.add(cp)
                pp = j.get("parent") or ""
                if pp and stage.GetPrimAtPath(pp).IsValid():
                    parents_set.add(pp)

        # ── 2) ArticulationRoot 가 붙을 조상 Xform 결정(문서 §5: 움직이는 링크엔 금지, 베이스의 '부모') ──
        anc = _common_prim_ancestor(stage, list(children_set | parents_set))
        if anc and anc.IsValid() and anc.GetPath().pathString in children_set:
            anc = anc.GetParent()                       # 공통조상이 움직이는 링크면 그 부모로
        if not (anc and anc.IsValid()) or anc.GetPath() == Sdf.Path("/"):
            dp = stage.GetDefaultPrim()
            anc = dp if (dp and dp.IsValid() and dp.GetPath() != Sdf.Path("/")) else None
        if not (anc and anc.IsValid()) or anc.GetPath() == Sdf.Path("/"):
            c0 = stage.GetPrimAtPath(sorted(children_set)[0]) if children_set else None
            anc = c0.GetParent() if (c0 and c0.IsValid()) else None
        if not (anc and anc.IsValid()) or anc.GetPath() == Sdf.Path("/"):
            anc = UsdGeom.Xform.Define(stage, "/World").GetPrim()
        if anc.GetTypeName() in ("", "Scope"):           # root 는 Xform 이어야(문서 §5.5)
            anc.SetTypeName("Xform")
        art_root = anc.GetPath().pathString
        joints_scope = f"{art_root}/Joints"
        UsdGeom.Scope.Define(stage, joints_scope)

        # ── 3) 조인트 author (body0=base, body1=child / 앵커정합 / limit / mass / drive target=0) ──
        authored = []
        bl_idx = 0
        moving_bases: set[str] = set()   # revolute/prismatic 의 base → world 에 FixedJoint 로 고정 대상
        movable_children: set[str] = set()  # revolute/prismatic 의 가동 자식 → 충돌 필터 대상
        for i, j in enumerate(vjoints):
            child_p = j["child"]; cprim = stage.GetPrimAtPath(child_p)
            apply_body(child_p)
            parent_p = j.get("parent") or ""
            pvalid = bool(parent_p) and stage.GetPrimAtPath(parent_p).IsValid()
            jtype = (j.get("type") or "revolute").lower()
            jaxis = (j.get("axis") or "").upper()
            axis = jaxis if jaxis in ("X", "Y", "Z") else up_axis   # 미지정이면 up-axis(문서 §1)

            # base 결정: 부모 있으면 부모, 없으면 base_link 합성(문서 §5: 최소 2-링크)
            if pvalid:
                apply_body(parent_p); base_p = parent_p
            elif jtype in ("revolute", "prismatic"):
                base_p = f"{art_root}/base_link" + ("" if bl_idx == 0 else f"_{bl_idx}"); bl_idx += 1
                blx = UsdGeom.Xform.Define(stage, base_p)
                UsdPhysics.RigidBodyAPI.Apply(blx.GetPrim())
                UsdPhysics.MassAPI.Apply(blx.GetPrim()).CreateMassAttr(1.0)
            else:
                base_p = ""   # fixed 조인트인데 부모 없음 → 월드 고정

            jp = f"{joints_scope}/joint_{i}"
            if jtype == "prismatic":
                J = UsdPhysics.PrismaticJoint.Define(stage, jp); J.CreateAxisAttr(axis)
            elif jtype == "fixed":
                J = UsdPhysics.FixedJoint.Define(stage, jp)
            else:
                jtype = "revolute"; J = UsdPhysics.RevoluteJoint.Define(stage, jp); J.CreateAxisAttr(axis)

            # 앵커 정합(문서 §3): 두 앵커가 같은 월드 경첩 프레임(위치+회전)을 가리키게
            pw = piv_world(j, cprim)
            w_c = xcache.GetLocalToWorldTransform(cprim)
            lp1 = w_c.GetInverse().Transform(pw)
            J.CreateBody1Rel().SetTargets([Sdf.Path(child_p)])
            J.CreateLocalPos1Attr(Gf.Vec3f(lp1[0], lp1[1], lp1[2]))
            J.CreateLocalRot1Attr(_quatf_inverse_of_world_rot(w_c, Gf))
            if base_p:
                bprim = stage.GetPrimAtPath(base_p)
                J.CreateBody0Rel().SetTargets([Sdf.Path(base_p)])
                w_b = xcache.GetLocalToWorldTransform(bprim)
                lp0 = w_b.GetInverse().Transform(pw)
                J.CreateLocalRot0Attr(_quatf_inverse_of_world_rot(w_b, Gf))
            else:
                lp0 = pw
                J.CreateLocalRot0Attr(Gf.Quatf(1.0, 0.0, 0.0, 0.0))
            J.CreateLocalPos0Attr(Gf.Vec3f(lp0[0], lp0[1], lp0[2]))

            lo, hi = j.get("lower"), j.get("upper")
            if jtype in ("revolute", "prismatic") and lo is not None and hi is not None:
                lo, hi = float(lo), float(hi)
                if lo > hi:
                    lo, hi = hi, lo
                if lo != hi:
                    J.CreateLowerLimitAttr(lo); J.CreateUpperLimitAttr(hi)
            _apply_mass(cprim, UsdPhysics)
            if base_p:
                _apply_mass(stage.GetPrimAtPath(base_p), UsdPhysics)
            if drive:
                _apply_drive(J, jtype, lo, UsdPhysics)   # 위치드라이브 target=0(닫힘) (문서 §6b/§8). 옵션.
            if jtype in ("revolute", "prismatic"):
                movable_children.add(child_p)
                if base_p:
                    moving_bases.add(base_p)
            authored.append({"name": f"joint_{i}", "type": jtype, "axis": axis,
                             "parent": (parent_p or base_p or "(world)"), "child": child_p, "lower": lo, "upper": hi})

        # ── 4) fixed base: 베이스(가동 조인트의 부모 중 누구의 자식도 아닌 링크)를 world 에 FixedJoint 로 고정.
        # Omniverse 에서 articulation 이 '고정 베이스'가 되려면 베이스 링크 ↔ world 의 FixedJoint 가 필요하다
        # (없으면 floating base → 앵커가 없어 중력에 떨어지며 엎어진다).
        base_links = sorted(moving_bases - children_set)
        for n, b in enumerate(base_links):
            bprim = stage.GetPrimAtPath(b)
            if bprim and bprim.IsValid():
                UsdPhysics.RigidBodyAPI.Apply(bprim)
                _apply_mass(bprim, UsdPhysics)
                fj = UsdPhysics.FixedJoint.Define(stage, f"{joints_scope}/base_fix_{n}")
                fj.CreateBody1Rel().SetTargets([Sdf.Path(b)])   # body0 미지정 = world → 베이스 고정

        # ── 4.5) loose 자유 강체 처리 (loose_mode) ──
        # 입력이 이미 강체를 갖고 있으면(예: 물성 카드 산출물은 모든 부품에 RigidBodyAPI) 관절에 안 붙은
        # 강체는 중력에 떨어져 나간다. 두 가지 처리 모드:
        #
        #  • loose_mode="weld" (기본): 자유 강체를 **베이스 링크에 FixedJoint 로 용접** → 강체로 유지한 채
        #    아티큘레이션의 고정 링크가 되어 떨어지지도, 월드에 박히지도 않는다. (월드에 직접 FixedJoint 로
        #    묶으면 한 아티큘레이션에 '고정 베이스'가 여러 개 생겨 솔버 NaN(PxArticulationLink force invalid)이
        #    나므로, world 가 아니라 베이스 '링크'에 묶는 것이 핵심.) 부품이 여러 메시로 쪼개진 본체도
        #    한 덩어리로 떨어진다. ← 권장.
        #  • loose_mode="static": (과거 동작) RigidBodyAPI 를 떼 정적 콜라이더로 강등(월드 고정, 충돌만 유지).
        #    용접 대상 베이스가 없거나, 정적 환경물이 섞여 들어와 일부러 박아두고 싶을 때의 안전 폴백.
        notes: list[str] = []
        welded_loose: list[str] = []      # 용접(강체 유지)된 부품 이름
        demoted_loose: list[str] = []     # 정적 강등된 부품 이름
        welded_loose_paths: list[str] = []  # 충돌 필터 대상(용접/강등 공통)
        if weld_loose:
            jointed = set(children_set) | set(parents_set) | set(moving_bases)
            # 용접 대상 = 1순위 고정 베이스 링크, 없으면 아무 jointed 링크. 둘 다 없으면 용접 불가 → 강등 폴백.
            weld_target = base_links[0] if base_links else (sorted(jointed)[0] if jointed else None)
            phys_apis = [UsdPhysics.RigidBodyAPI]
            try:
                from pxr import PhysxSchema
                phys_apis.append(PhysxSchema.PhysxRigidBodyAPI)
            except Exception:  # noqa: BLE001
                pass
            n_loose = 0
            for prim in list(stage.Traverse()):
                if not (prim.IsValid() and prim.HasAPI(UsdPhysics.RigidBodyAPI)):
                    continue
                pth = prim.GetPath().pathString
                if pth in jointed or "/base_link" in pth:
                    continue
                n_loose += 1
                if loose_mode == "weld" and weld_target and pth != weld_target:
                    # 베이스 링크에 FixedJoint 로 용접 → 강체 유지(동적). world 가 아니라 '링크'에 묶는다.
                    fj = UsdPhysics.FixedJoint.Define(stage, f"{joints_scope}/weld_loose_{n_loose}")
                    fj.CreateBody0Rel().SetTargets([Sdf.Path(weld_target)])
                    fj.CreateBody1Rel().SetTargets([Sdf.Path(pth)])
                    welded_loose.append(prim.GetName())
                else:
                    # 정적 강등(폴백/옵션): RigidBodyAPI 제거 → 월드 고정 콜라이더.
                    for api in phys_apis:
                        try:
                            if prim.HasAPI(api):
                                prim.RemoveAPI(api)
                        except Exception:  # noqa: BLE001
                            pass
                    demoted_loose.append(prim.GetName())
                welded_loose_paths.append(pth)

        # ── 5) ArticulationRootAPI 는 조상 Xform(anc)에 정확히 하나(NVIDIA BA.001). world FixedJoint(§4)가
        # 고정 베이스를 만든다. (움직이는 링크에 직접 걸지 않는다.)
        try:
            UsdPhysics.ArticulationRootAPI.Apply(anc)
        except Exception:  # noqa: BLE001
            pass

        # ── 5.5) 질량속성 단위 보정: 입력의 com/관성이 미터로 기록됐는데 스테이지가 mm 면(물성 카드 단위
        # 버그) PhysX 가 관성을 ~10^6배 작게 읽어 회전을 못 잡고 폭주·전복한다. → com/관성을 비워 PhysX 가
        # 충돌 형상에서 스테이지 단위로 재계산하게 한다(mass[kg]는 길이단위 무관이라 유지).
        for prim in stage.Traverse():
            if not prim.HasAPI(UsdPhysics.RigidBodyAPI):
                continue
            mp = UsdPhysics.MassAPI(prim)
            for attr in (mp.GetCenterOfMassAttr(), mp.GetDiagonalInertiaAttr(), mp.GetPrincipalAxesAttr()):
                try:
                    if attr and attr.HasAuthoredValue():
                        attr.Clear()
                except Exception:  # noqa: BLE001
                    pass

        if welded_loose:
            uniq = sorted(set(welded_loose))
            notes.append("관절이 없는 강체 부품 %d개를 베이스에 고정(용접)해 강체로 유지하면서 떨어지지 않게 했습니다: %s%s — 본체가 여러 메시로 쪼개져 있어도 한 덩어리로 거동합니다."
                         % (len(uniq), ", ".join(uniq[:8]), " 외…" if len(uniq) > 8 else ""))
        if demoted_loose:
            uniq = sorted(set(demoted_loose))
            notes.append("관절이 없는 강체 부품 %d개를 정적(고정) 콜라이더로 강등해 떨어지지 않게 했습니다: %s%s — 이 부품을 움직이게 하려면 관절을 추가하세요."
                         % (len(uniq), ", ".join(uniq[:8]), " 외…" if len(uniq) > 8 else ""))

        # ── 6) 충돌 근사 자동 판별: 오목(속 빈/컨테이너)은 convexDecomposition 으로 → Play 시 파고들어 폭발하는 것 방지 ──
        n_cvx, n_dec = _auto_collisions(stage, UsdGeom, UsdPhysics, Usd)
        if n_dec:
            notes.append("충돌 형상 자동 판별: 볼록 부품 %d개는 convexHull, 오목(속 빈/컨테이너) 부품 %d개는 convexDecomposition 으로 처리해 부품끼리 파고들어 튕겨나가는 것을 막았습니다."
                         % (n_cvx, n_dec))

        # ── 7) 충돌 필터(자기 에셋 내부) — 가동부가 하우징/핀/다른 가동부와 닿아 '떨림(jitter)'나는 것 방지.
        # 조인트가 운동을 잡아주므로 자기 에셋 부품끼리는 충돌 불필요(NVIDIA ControlBox 정품도 동일하게
        # 문↔하우징 FilteredPairs 적용). 외부 물체와의 충돌은 유지된다.
        if hasattr(UsdPhysics, "FilteredPairsAPI") and movable_children:
            parts = sorted(set(children_set) | set(parents_set) | set(moving_bases) | set(welded_loose_paths))
            for c in sorted(movable_children):
                cprim = stage.GetPrimAtPath(c)
                if not (cprim and cprim.IsValid()):
                    continue
                rel = UsdPhysics.FilteredPairsAPI.Apply(cprim).CreateFilteredPairsRel()
                for other in parts:
                    if other != c:
                        rel.AddTarget(Sdf.Path(other))
            notes.append("가동부가 하우징·힌지 핀과 닿아 떨리지 않도록, 같은 에셋 부품끼리의 충돌을 필터링했습니다(외부 물체와의 충돌은 유지).")

        # 아티큘레이션 자기충돌 비활성(추가 안전) — 직접 연결 링크 외의 내부 충돌 떨림 차단.
        try:
            from pxr import PhysxSchema
            PhysxSchema.PhysxArticulationAPI.Apply(anc).CreateEnabledSelfCollisionsAttr(False)
        except Exception:  # noqa: BLE001
            pass

        if _usdz_ctx is not None:
            # usdz 입력: 원본 패키지(MDL·텍스처) 그대로 보존하고 수정된 .usd 만 교체 → 재질 100% 유지
            # (_finalize 의 재앵커는 MDL 내부 텍스처를 흘려 룩이 깨졌었음)
            import zipfile
            _wd, _arcs, _usd_arc = _usdz_ctx
            stage.GetRootLayer().Save()               # 조인트/물리 편집 반영
            fd3, outz = tempfile.mkstemp(suffix=".usdz"); os.close(fd3)
            with zipfile.ZipFile(outz, "w", zipfile.ZIP_STORED) as _z:
                _z.write(path, _usd_arc)              # 수정 .usd 먼저
                for _n in _arcs:
                    if _n == _usd_arc:
                        continue
                    _z.write(os.path.join(_wd, _n), _n)
            with open(outz, "rb") as f:
                data = f.read()
            os.remove(outz)
            return data, authored, notes, ".usdz"
        stage.Export(outp)
        data, ext = _finalize_self_contained(outp)   # (비-usdz) 입력 temp 살아있는 동안 자기완결화(.usdz or .usd)
        return data, authored, notes, ext
    finally:
        for p in (path, outp):
            try:
                os.remove(p)
            except OSError:
                pass
        if _usdz_ctx is not None:
            import shutil
            shutil.rmtree(_usdz_ctx[0], ignore_errors=True)


def _author_flat(meshes: list[dict], joints: list[dict[str, Any]], drive: bool = True) -> tuple[bytes, list[dict[str, Any]], list[str]]:
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
    # ArticulationRoot 는 아래(고정 베이스 링크)에서 적용 — /World(Xform)에 걸면 floating base 라 엎어진다.

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
    children_set: set[str] = set()
    moving_bases: set[str] = set()   # revolute/prismatic 의 base 바디 prim 경로 → world 에 고정
    bl_idx = 0
    for i, j in enumerate(joints):
        child = j.get("child")
        if child not in body_prim:
            continue
        children_set.add(child)
        jtype = (j.get("type") or "revolute").lower()
        jaxis = (j.get("axis") or "").upper()
        axis = jaxis if jaxis in ("X", "Y", "Z") else "Z"   # 미지정이면 up-axis(여긴 Z-up)
        piv = np.asarray(j["pivot"], float) if j.get("pivot") else body_centroid[child]
        jp = f"/World/Joints/joint_{i}"
        if jtype == "prismatic":
            J = UsdPhysics.PrismaticJoint.Define(stage, jp); J.CreateAxisAttr(axis)
        elif jtype == "fixed":
            J = UsdPhysics.FixedJoint.Define(stage, jp)
        else:
            jtype = "revolute"; J = UsdPhysics.RevoluteJoint.Define(stage, jp); J.CreateAxisAttr(axis)
        # base 결정: 부모 있으면 부모 바디, 없으면 base_link 합성(문서 §5: 최소 2-링크)
        parent = j.get("parent") or ""
        if parent and parent in body_prim:
            base_bp = body_prim[parent]
        elif jtype in ("revolute", "prismatic"):
            base_bp = "/World/base_link" + ("" if bl_idx == 0 else f"_{bl_idx}"); bl_idx += 1
            blx = UsdGeom.Xform.Define(stage, base_bp)
            UsdPhysics.RigidBodyAPI.Apply(blx.GetPrim())
            UsdPhysics.MassAPI.Apply(blx.GetPrim()).CreateMassAttr(1.0)
        else:
            base_bp = ""
        if base_bp:
            J.CreateBody0Rel().SetTargets([base_bp])
        # 바디가 회전 없는 Xform(로컬=월드)이라 두 앵커가 같은 월드점, 회전 identity 로 이미 정합(문서 §3).
        J.CreateBody1Rel().SetTargets([body_prim[child]])
        J.CreateLocalPos0Attr(Gf.Vec3f(float(piv[0]), float(piv[1]), float(piv[2])))
        J.CreateLocalPos1Attr(Gf.Vec3f(float(piv[0]), float(piv[1]), float(piv[2])))
        lo, hi = j.get("lower"), j.get("upper")
        if jtype in ("revolute", "prismatic") and lo is not None and hi is not None:
            lo, hi = float(lo), float(hi)
            if lo > hi:                       # 방어적: 뒤집혀 오면 교정
                lo, hi = hi, lo
            if lo != hi:                      # 같으면 한계 미설정(자유) — 잠김 방지
                J.CreateLowerLimitAttr(lo); J.CreateUpperLimitAttr(hi)
        _apply_mass(stage.GetPrimAtPath(body_prim[child]), UsdPhysics)
        if base_bp:
            _apply_mass(stage.GetPrimAtPath(base_bp), UsdPhysics)
        if drive:
            _apply_drive(J, jtype, lo, UsdPhysics)  # 위치드라이브 target=0 (문서 §6b). 옵션.
        if jtype in ("revolute", "prismatic") and base_bp:
            moving_bases.add(base_bp)
        authored.append({"name": f"joint_{i}", "type": jtype, "axis": axis,
                         "parent": parent or "(world)", "child": child, "lower": lo, "upper": hi})

    # fixed base: 베이스 링크 ↔ world FixedJoint(앵커) + artRoot 는 /World 에 하나.
    child_bps = {body_prim[c] for c in children_set if c in body_prim}
    for n, b in enumerate(sorted(moving_bases - child_bps)):
        fj = UsdPhysics.FixedJoint.Define(stage, f"/World/Joints/base_fix_{n}")
        fj.CreateBody1Rel().SetTargets([b])   # body0 미지정 = world → base 고정
    UsdPhysics.ArticulationRootAPI.Apply(world.GetPrim())

    # 충돌 근사 자동 판별(오목→convexDecomposition) — author_preserve 와 동일.
    _auto_collisions(stage, UsdGeom, UsdPhysics, Usd)

    # 느슨한 조각 경고(문서 §8): 어떤 바디에도 안 속한 메시(/World/rest 로 분류된 것).
    notes: list[str] = []
    if rest:
        names = sorted({m.get("name", "?") for m in rest})[:8]
        notes.append("어떤 관절에도 안 붙은 조각이 있습니다(움직일 때 따로 떨어져 보일 수 있음): "
                     + ", ".join(names) + " — 함께 움직여야 하면 해당 부품을 바디 노드에 포함하세요.")

    # .usd(바이너리 crate)로 내보내기 — 텍스트(.usda)보다 작고 빠름. 자기완결.
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
    return data, authored, notes


# ───────────────────────── AI(Claude) 관절 추론 ─────────────────────────
_AI_JOINTS_SYS = (
    "You are a mechanical/robotics engineer configuring a USD articulation for one assembly. "
    "You are given its parts, each with a world-space axis-aligned bounding box in METERS "
    "(center_m, size_m) and the exact USD prim path. Decide which parts are MOVABLE relative to "
    "the rest of the assembly (doors, lids, hatches, covers, drawers, sliders, handles, levers, "
    "wheels, hinged or sliding panels) and propose one physics joint per movable part.\n"
    "RULES:\n"
    "- Use ONLY the exact 'path' strings given, verbatim, for child and parent.\n"
    "- child = the movable part's path.\n"
    "- parent = the path of the static body it attaches to (e.g. the housing/frame/body). "
    "Use \"\" (empty string) if it attaches to the world/ground.\n"
    "- type = 'revolute' (hinge/rotation), 'prismatic' (linear slide), or 'fixed' (rigid weld).\n"
    "- axis = 'X', 'Y', or 'Z': the WORLD axis the part rotates about (revolute) or slides along "
    "(prismatic). Infer from the part's shape and how it realistically moves (a tall door swings "
    "about a vertical axis; a drawer slides along its longest horizontal axis).\n"
    "- pivot = [x,y,z] in WORLD METERS. For revolute put it ON THE HINGE EDGE of the part (NOT the "
    "center) — pick the bbox face/edge nearest the parent body. For prismatic, any point on the "
    "slide axis. Omit for fixed.\n"
    "- lower/upper = motion range. revolute in DEGREES (e.g. a door 0..110), prismatic in METERS. "
    "Keep lower < upper and realistic.\n"
    "- Leave purely structural/static parts UN-jointed. Do NOT invent movement.\n"
    "- If nothing is clearly movable, return an empty joints list.\n"
    "Return STRICT JSON ONLY, no prose:\n"
    '{"joints":[{"child":"<path>","parent":"<path or empty>","type":"revolute","axis":"Z",'
    '"pivot":[x,y,z],"lower":0,"upper":110,"reason":"<short why>"}]}'
)


def _ai_bodies(tree: list[dict], meshes: list[dict], max_n: int = 220) -> list[dict]:
    """LLM 에 줄 '부품 후보' 목록 — 트리 노드 중 메시를 품은 것 + 메시 리프, 각각 월드 AABB(m).
    덩치 큰 컨테이너 노드(메시 16개 초과, Mesh 아님)는 제외해 '부품 단위' 선택을 유도한다."""
    mb: dict[str, tuple] = {}
    for m in meshes:
        V = np.asarray(m["vertices"], float).reshape(-1, 3)
        if V.size:
            mb[m["path"]] = (V.min(0), V.max(0))

    nodes: list[dict] = []
    def _walk(ns: list[dict]) -> None:
        for n in ns:
            nodes.append(n)
            _walk(n.get("children") or [])
    _walk(tree)

    def _entry(path, name, typ, paths):
        lo = np.min([mb[p][0] for p in paths], axis=0)
        hi = np.max([mb[p][1] for p in paths], axis=0)
        c = (lo + hi) * 0.5
        s = hi - lo
        return {"path": path, "name": name or path, "type": typ or "Xform",
                "center_m": [round(float(x), 4) for x in c],
                "size_m": [round(float(x), 4) for x in s], "mesh_count": len(paths)}

    out: list[dict] = []
    seen: set[str] = set()
    for n in nodes:
        npath = n["path"]
        under_m = [p for p in mb if p == npath or p.startswith(npath.rstrip("/") + "/")]
        if not under_m:
            continue
        is_mesh = (n.get("type") == "Mesh")
        if not is_mesh and len(under_m) > 16:   # 큰 컨테이너(예: /door 전체)는 제외
            continue
        out.append(_entry(npath, n.get("name"), n.get("type"), under_m))
        seen.add(npath)
    for m in meshes:        # 트리에 없던(프록시 등) 메시 경로 보강
        if m["path"] not in seen and m["path"] in mb:
            out.append(_entry(m["path"], m["name"], "Mesh", [m["path"]]))
            seen.add(m["path"])
    if len(out) > max_n:    # 너무 많으면 작은 부품(가동부 후보) 우선
        out = sorted(out, key=lambda b: b["mesh_count"])[:max_n]
    return out


async def ai_suggest_joints(
    file_bytes: bytes, name: str, *, context: str = "",
    images: list[tuple[bytes, str]] | None = None,
    api_key: str = "", oauth_token: str = "", model: str = "claude-opus-4-8",
) -> tuple[list[dict[str, Any]], str | None, int]:
    """Claude 가 부품 AABB·계층(+선택: 실물 참조 이미지)을 보고 관절(child/parent/type/axis/pivot/limit/reason)을 추론.
    반환 joints 는 프런트 편집기·/build 스키마와 동일 → 사용자가 검토·수정 후 생성한다."""
    if not (api_key or oauth_token):
        raise ValueError("AI 추론용 Claude 자격증명이 없습니다(.env CLAUDE_CODE_OAUTH_TOKEN).")
    from ..mass_physics.pipeline import _call_llm

    tree, meshes, _note, _up = extract(file_bytes, name)
    bodies = _ai_bodies(tree, meshes)
    valid = {b["path"] for b in bodies}
    imgs = images or []
    user = ((f"Context: {context}\n\n" if context else "")
            + ("Reference photos of the real assembly are attached — use them to judge which parts "
               "move (doors/lids/drawers/wheels), the motion type, and hinge/slide direction.\n\n" if imgs else "")
            + f"Assembly has {len(bodies)} candidate parts. World AABB per part (meters):\n"
            + json.dumps(bodies, ensure_ascii=False))
    data = await _call_llm(_AI_JOINTS_SYS, user, api_key, oauth_token, model, images=imgs)
    raw = data.get("joints") if isinstance(data, dict) else None

    out: list[dict[str, Any]] = []
    for j in (raw or []):
        if not isinstance(j, dict):
            continue
        child = str(j.get("child") or "").strip()
        if child not in valid:
            continue
        parent = str(j.get("parent") or "").strip()
        if parent not in valid or parent == child:
            parent = ""
        jt = (str(j.get("type") or "revolute")).lower()
        if jt not in ("revolute", "prismatic", "fixed"):
            jt = "revolute"
        ax = (str(j.get("axis") or "")).upper()
        if ax not in ("X", "Y", "Z"):
            ax = "Z"
        pivot = None
        piv = j.get("pivot")
        if isinstance(piv, (list, tuple)) and len(piv) == 3:
            try:
                pivot = [float(piv[0]), float(piv[1]), float(piv[2])]
            except (TypeError, ValueError):
                pivot = None
        rec: dict[str, Any] = {"type": jt, "parent": parent, "child": child, "axis": ax,
                               "pivot": pivot, "reason": str(j.get("reason") or "")[:300]}
        if jt != "fixed":
            lo, hi = j.get("lower"), j.get("upper")
            try:
                if lo is not None and hi is not None:
                    lo, hi = float(lo), float(hi)
                    if lo > hi:
                        lo, hi = hi, lo
                    if lo != hi:
                        rec["lower"], rec["upper"] = lo, hi
            except (TypeError, ValueError):
                pass
        out.append(rec)

    note = None
    if not out:
        note = "AI가 움직일 만한 부품을 찾지 못했습니다(또는 응답이 비었음). 수동으로 지정해 주세요."
    return out, note, len(bodies)


def viewer_glb(meshes: list[dict]) -> bytes:
    import trimesh

    scene = trimesh.Scene()
    for m in meshes:
        V = np.asarray(m["vertices"], float).reshape(-1, 3)
        F = np.asarray(m["faces"], int).reshape(-1, 3)
        scene.add_geometry(trimesh.Trimesh(vertices=V, faces=F), node_name=_safe(m["name"]))
    return scene.export(file_type="glb")
