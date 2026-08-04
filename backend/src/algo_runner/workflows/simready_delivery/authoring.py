"""Stage B — 비준수 USD 를 SimReady 프로파일 준수로 만드는 코드별 FIX(pxr 변형, in-process).

각 fix(ctx) → 설명 문자열. fix_rack.py(검증 통과 레시피)를 임의 에셋용으로 일반화.
사용자 원본은 건드리지 않음 — 세션 작업본에만 적용.
"""
from __future__ import annotations

import datetime
import os
import re as _re
import shutil
import urllib.request as _urlreq
from pathlib import Path
from typing import Any, Callable

# code → 같은 함수를 공유하는 그룹. "all" 적용 시 이 순서대로(앵커→텍스처보정→나머지).
FIX_ORDER = ["UN.007", "COL.001", "RB.MB.001", "PMT.001", "AA.001", "VM.MDL.001",
             "VM.TEX.001", "VM.TEX.002", "VM.MAT.001", "GSP.001", "NP.006"]


def _is_remote(p: str) -> bool:
    return p.lower().startswith(("http:/", "https:/"))


# ── 검색경로 MDL(OmniPBR.mdl 등) 해결 ────────────────────────────────────────
# NVIDIA 원본 OEM 자산은 코어 MDL 을 폴더 없는 '검색경로'로 참조한다(@OmniPBR.mdl@).
# AA.001 은 이를 경고로만 보지만(원문: "only MDL search paths are allowed"),
# VM.MDL.001 은 './' 앵커 + **파일 실존**을 요구하므로 그대로 두면 검증이 깨진다.
# → 설치된 Kit/Isaac 의 코어 MDL 을 찾아 ./materials/ 로 복사해 앵커한다.
def _mdl_search_roots() -> list[Path]:
    """이 머신에 설치된 Kit/Isaac 코어 MDL 디렉터리들(있는 것만)."""
    roots: list[Path] = []
    env_extra = os.environ.get("MDL_SYSTEM_PATH", "") or os.environ.get("MDL_USER_PATH", "")
    for chunk in env_extra.split(os.pathsep):
        if chunk.strip():
            roots.append(Path(chunk.strip()))
    for base in sorted(Path("C:/").glob("isaacsim*")) + [Path.home() / "AppData/Local/ov/pkg"]:
        if not base.exists():
            continue
        for sub in ("sim/kit/mdl/core/Base", "sim/kit/mdl/core", "kit/mdl/core/Base"):
            p = base / sub
            if p.is_dir():
                roots.append(p)
        roots.extend(d for d in base.glob("*/kit/mdl/core/Base") if d.is_dir())
    ovd = Path.home() / "AppData/Local/ov/data/exts"
    if ovd.is_dir():
        roots.extend(sorted(ovd.glob("v2/omni.kit.material.library*/data/tests/mtl"))[:1])
    return [r for r in roots if r.is_dir()]


def _copy_mdl_closure(src: Path, matdir: Path) -> list[str]:
    """MDL 본체 + 형제 모듈을 ./materials/ 로 복사.

    VM.MDL.001 이 요구하는 것은 '그 경로에 파일이 존재'하는 것이다. 그러므로
      * 이미 같은 크기 사본이 있으면 성공으로 보고 건너뛴다,
      * Windows 파일 잠금(WinError 32 — 백신·인덱서·잔류 Kit 프로세스)은 바이트 쓰기로 재시도,
      * 그래도 실패했지만 파일이 이미 있으면 성공으로 본다.
    이렇게 해야 잠금 한 번에 경로 앵커링까지 건너뛰어 VM.MDL.001/NP.008 이 남는 일이 없다(실측 버그)."""
    import time as _time

    out: list[str] = []
    for mod in _mdl_sibling_closure(src):
        dst = matdir / mod.name
        try:
            if dst.exists() and dst.stat().st_size == mod.stat().st_size:
                out.append(mod.name)
                continue
        except OSError:
            pass
        for _ in range(3):
            try:
                shutil.copy2(mod, dst)
                out.append(mod.name)
                break
            except OSError:
                try:
                    dst.write_bytes(mod.read_bytes())   # copystat 없이 내용만
                    out.append(mod.name)
                    break
                except OSError:
                    _time.sleep(0.3)
        else:
            if dst.exists():                            # 내용이 이미 있으면 요구사항은 충족
                out.append(mod.name)
    return out


def _mdl_sibling_closure(main: Path) -> list[Path]:
    """MDL 이 상대 import 하는 형제 모듈까지 재귀 수집.
    OmniPBR.mdl 은 OmniPBR_ClearCoat / OmniPBRBase 를 import 한다 — 본체만 복사하면
    수신측에서 MDL 컴파일이 실패해 룩이 깨진다. '::' 로 시작하는 절대 모듈
    (::df, ::base, ::nvidia::core_definitions …)은 런타임 설치본이 해결하므로 제외."""
    out: dict[str, Path] = {}
    todo = [main]
    while todo:
        cur = todo.pop()
        if cur.name in out or not cur.exists():
            continue
        out[cur.name] = cur
        try:
            txt = cur.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _re.finditer(r"^\s*(?:import|using)\s+(::)?([A-Za-z_]\w*)", txt, _re.M):
            if m.group(1):                      # 절대 모듈 → 설치본이 해결
                continue
            sib = cur.parent / (m.group(2) + ".mdl")
            if sib.exists():
                todo.append(sib)
    return list(out.values())


def _norm_url(p: str) -> str:
    """USD 가 정규화한 'https:/...'(슬래시 1개)를 정상 URL 로 복원."""
    if p.startswith(("https://", "http://")):
        return p
    if p.startswith("https:/"):
        return "https://" + p[len("https:/"):]
    if p.startswith("http:/"):
        return "http://" + p[len("http:/"):]
    return p


def _fetch(url: str, dst: Path) -> bool:
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        _urlreq.urlretrieve(url, str(dst))  # noqa: S310 (공개 NVIDIA 콘텐츠)
        return True
    except Exception:  # noqa: BLE001
        return False


class _Ctx:
    def __init__(self, stage: Any, asset_dir: Path, usd_name: str, cfg: dict[str, Any]):
        self.stage = stage
        self.asset_dir = asset_dir
        self.usd_name = usd_name
        self.cfg = cfg


def _meshes(stage: Any) -> list[Any]:
    from pxr import UsdGeom
    return [p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)]


def _gprims(stage: Any) -> list[Any]:
    from pxr import UsdGeom
    return [p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh) or p.IsA(UsdGeom.BasisCurves)]


def _materials(stage: Any) -> list[Any]:
    from pxr import UsdShade
    return [UsdShade.Material(p) for p in stage.Traverse() if p.IsA(UsdShade.Material)]


# 텍스처(asset 타입 셰이더 입력) — info:mdl:sourceAsset(모듈 자체)은 제외.
_ALBEDO_HINTS = ("albedo", "basecolor", "base_color", "diffuse", "color", "super_albedo")


def _is_albedo_input(name: str) -> bool:
    n = name.lower()
    if any(k in n for k in ("normal", "roughness", "metallic", "metalness", "occlusion",
                            "displacement", "orm", "height", "opacity", "ao")):
        return False
    return any(k in n for k in _ALBEDO_HINTS)


def _texture_inputs(stage: Any) -> list[tuple[Any, Any]]:
    """[(shader_prim, asset_attr)] — 셰이더의 asset 타입 입력(텍스처). MDL 모듈 경로는 제외."""
    from pxr import Sdf, UsdShade
    out = []
    for prim in stage.Traverse():
        if not prim.IsA(UsdShade.Shader):
            continue
        for attr in prim.GetAttributes():
            nm = attr.GetName()
            if nm == "info:mdl:sourceAsset" or not nm.startswith("inputs:"):
                continue
            if attr.GetTypeName() == Sdf.ValueTypeNames.Asset and attr.Get() is not None:
                out.append((prim, attr))
    return out


def _world_bbox(stage: Any) -> tuple[list[float], list[float]]:
    from pxr import Usd, UsdGeom
    import numpy as np
    lo = [1e30, 1e30, 1e30]; hi = [-1e30, -1e30, -1e30]
    for prim in _meshes(stage):
        m = UsdGeom.Mesh(prim)
        pts = m.GetPointsAttr().Get()
        if not pts:
            continue
        xf = np.array(UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default()), float)
        P = (np.c_[np.array(pts, float), np.ones(len(pts))] @ xf)[:, :3]
        lo = [min(lo[i], float(P[:, i].min())) for i in range(3)]
        hi = [max(hi[i], float(P[:, i].max())) for i in range(3)]
    if lo[0] > hi[0]:
        return [0, 0, 0], [1, 1, 1]
    return lo, hi


# ─────────────────────────── 개별 FIX ───────────────────────────
def fix_stage_units(ctx: _Ctx) -> str:
    """UN.001/002/005/006/007 — metersPerUnit=1·upAxis=Z·tcps=24·defaultPrim, 그리고 단위변환(점·extent 스케일)."""
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics
    stage = ctx.stage
    msgs = []
    rl = stage.GetRootLayer()
    cur = UsdGeom.GetStageMetersPerUnit(stage)
    orig_up = UsdGeom.GetStageUpAxis(stage)   # Z 로 강제하기 전에 원본 up축 캡처(Y-up 회전 판단용)
    # 기본 자동: 변환 배율 = 파일이 선언한 metersPerUnit(=cur). 사용자가 수동값을 주면 그걸 강제.
    raw = ctx.cfg.get("source_meters_per_unit")
    if raw in (None, "", "auto"):
        scale = cur
    else:
        try:
            scale = float(raw)
        except (TypeError, ValueError):
            scale = cur
    already = bool((rl.customLayerData or {}).get("sr_card_unit_scaled"))
    if abs(cur - 1.0) > 1e-9 and not already and abs(scale - 1.0) > 1e-9:
        nm = 0
        for prim in _meshes(stage):
            m = UsdGeom.Mesh(prim)
            pts = m.GetPointsAttr().Get()
            if not pts:
                continue
            nm += 1
            sp = [Gf.Vec3f(p[0] * scale, p[1] * scale, p[2] * scale) for p in pts]
            m.GetPointsAttr().Set(sp)
            xs = [p[0] for p in sp]; ys = [p[1] for p in sp]; zs = [p[2] for p in sp]
            m.CreateExtentAttr([Gf.Vec3f(min(xs), min(ys), min(zs)), Gf.Vec3f(max(xs), max(ys), max(zs))])
        # v5 §13.3: 길이 단위로 표현된 것은 전부 같이 스케일 — 조인트 localPos0/1, 그래스프 커브 점.
        # (질량은 kg라 제외.) 안 하면 검증은 통과해도 조인트/파지 위치가 1000× 어긋남.
        nj = 0
        for prim in stage.Traverse():
            for an in ("physics:localPos0", "physics:localPos1"):
                a = prim.GetAttribute(an)
                if a and a.IsValid() and a.Get() is not None:
                    p = a.Get(); a.Set(Gf.Vec3f(p[0] * scale, p[1] * scale, p[2] * scale)); nj += 1
            if prim.IsA(UsdGeom.BasisCurves):
                cv = UsdGeom.BasisCurves(prim)
                cpts = cv.GetPointsAttr().Get()
                if cpts:
                    sp = [Gf.Vec3f(p[0] * scale, p[1] * scale, p[2] * scale) for p in cpts]
                    cv.GetPointsAttr().Set(sp)
                    xs = [p[0] for p in sp]; ys = [p[1] for p in sp]; zs = [p[2] for p in sp]
                    cv.CreateExtentAttr([Gf.Vec3f(min(xs), min(ys), min(zs)), Gf.Vec3f(max(xs), max(ys), max(zs))])
                wd = cv.GetWidthsAttr().Get()   # 굵기도 길이 단위 → 같이 스케일. 안 하면 1000× 남아 grasp 가 거대 구로 렌더됨.
                if wd:
                    cv.GetWidthsAttr().Set([float(w * scale) for w in wd])
        cld = dict(rl.customLayerData); cld["sr_card_unit_scaled"] = True; rl.customLayerData = cld
        msgs.append(f"좌표 ×{scale} 스케일(단위→m): 메시 {nm}개" + (f"·조인트 localPos {nj}개" if nj else "") + " 변환")
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    # Y-up 자산은 upAxis만 Z로 바꾸면 형상이 옆으로 눕는다(과거 컨트롤박스 버그). 루트에 rotateX(+90)로
    # 진짜 Z-up 화 + 중력 -Z + 월드고정 조인트 앵커 회전(안 하면 Play 때 원위치로 snap 돼 다시 누움).
    zup_done = bool((rl.customLayerData or {}).get("sr_card_zup_rotated"))
    if orig_up == UsdGeom.Tokens.y and not zup_done:
        dp = stage.GetDefaultPrim()
        if dp and dp.IsValid():
            UsdGeom.Xformable(dp).AddRotateXOp().Set(90.0)
            for pr in stage.Traverse():
                if pr.IsA(UsdPhysics.Scene):
                    pr.CreateAttribute("physics:gravityDirection", Sdf.ValueTypeNames.Vector3f).Set(Gf.Vec3f(0, 0, -1))
                elif pr.GetTypeName() == "PhysicsFixedJoint" and not pr.GetRelationship("physics:body0").GetTargets():
                    # 월드 고정 조인트: 월드 앵커 회전도 rotateX(+90)에 맞춰야 Play 때 안 눕는다.
                    pr.CreateAttribute("physics:localRot0", Sdf.ValueTypeNames.Quatf).Set(Gf.Quatf(0.70710678, 0.70710678, 0.0, 0.0))
            cld = dict(rl.customLayerData or {}); cld["sr_card_zup_rotated"] = True; rl.customLayerData = cld
            msgs.append("Y-up→Z-up 회전(rotateX+90)·중력 -Z·월드고정 앵커 보정")
    if not stage.GetTimeCodesPerSecond():
        stage.SetTimeCodesPerSecond(24)
    if not (stage.GetDefaultPrim() and stage.GetDefaultPrim().IsValid()):
        roots = [p for p in stage.GetPseudoRoot().GetChildren()]
        if roots:
            stage.SetDefaultPrim(roots[0])
            msgs.append(f"defaultPrim={roots[0].GetName()}")
    lo, hi = _world_bbox(stage)
    dims = [round(hi[i] - lo[i], 3) for i in range(3)]
    msgs.append(f"metersPerUnit=1·upAxis=Z·tcps=24 설정. 결과 크기≈{dims[0]}×{dims[1]}×{dims[2]} m (단위 확인 필요)")
    return "; ".join(msgs)


def fix_sdf(ctx: _Ctx) -> str:
    """COL.001 — PhysX 충돌 근사 sdf. CollisionAPI/MeshCollisionAPI + PhysxSDFMeshCollisionAPI(해상도).
    approximation='sdf' 만 두면 PhysX 가 SDF 를 못 굽고(동적 바디에서 'triangle mesh None → convexHull'
    폴백) 본체가 solid 가 돼 가동부가 막힌다. PhysX SDF 스키마+해상도를 함께 붙여야 실제 SDF 로 동작한다."""
    from pxr import Sdf, UsdPhysics
    n = 0
    for prim in _meshes(ctx.stage):
        UsdPhysics.CollisionAPI.Apply(prim)
        mc = UsdPhysics.MeshCollisionAPI.Apply(prim)
        mc.CreateApproximationAttr().Set("sdf")
        # PhysX SDF 스키마 + 해상도. AddAppliedSchema 는 이 venv 에 PhysxSchema 미등록이라 무시되므로,
        # apiSchemas 메타데이터에 직접 토큰을 넣는다(Isaac 은 PhysxSchema 가 있어 이 메타를 인식해 SDF 를 굽는다).
        op = prim.GetMetadata("apiSchemas")
        items = list(op.GetAddedOrExplicitItems()) if op else []
        if "PhysxSDFMeshCollisionAPI" not in items:
            prim.SetMetadata("apiSchemas", Sdf.TokenListOp.CreateExplicit(items + ["PhysxSDFMeshCollisionAPI"]))
        ra = prim.GetAttribute("physxSDFMeshCollision:sdfResolution")
        if not (ra and ra.HasAuthoredValue()):
            prim.CreateAttribute("physxSDFMeshCollision:sdfResolution", Sdf.ValueTypeNames.Int).Set(256)
        n += 1
    return f"충돌 메시 {n}개: sdf + PhysxSDFMeshCollisionAPI(해상도 256) — Isaac 이 실제 SDF 로 굽도록(convexHull 폴백 방지)"


def fix_rigidbody(ctx: _Ctx) -> str:
    """RB.MB.001 — 모든 메시에 RigidBodyAPI+MassAPI 보장(메시 ≥2면 멀티바디 충족)."""
    from pxr import UsdPhysics
    ms = _meshes(ctx.stage)
    nb = 0
    for prim in ms:
        UsdPhysics.RigidBodyAPI.Apply(prim)
        ma = UsdPhysics.MassAPI.Apply(prim)
        if ma.GetMassAttr().Get() in (None, 0):
            ma.CreateMassAttr().Set(1.0)
        nb += 1
    if nb < 2:
        return f"⚠ 강체 후보 메시가 {nb}개뿐 — 멀티바디(FET004)는 ≥2개 필요. 형상을 분리하거나 부품을 추가하세요(자동 합성 불가)."
    return f"강체(RigidBodyAPI)+질량 적용 — 메시 {nb}개, 각 1kg 기본(총 {nb}kg). 멀티바디 충족"


def _get_or_make_pmat(ctx: _Ctx) -> Any:
    from pxr import UsdPhysics, UsdShade
    stage = ctx.stage
    base = stage.GetDefaultPrim().GetPath().pathString if (stage.GetDefaultPrim() and stage.GetDefaultPrim().IsValid()) else ""
    path = f"{base}/PhysicsMaterials/default_physics"
    prim = stage.GetPrimAtPath(path)
    if not prim or not prim.IsValid():
        mat = UsdShade.Material.Define(stage, path)
        pm = UsdPhysics.MaterialAPI.Apply(mat.GetPrim())
        pm.CreateStaticFrictionAttr().Set(0.6)
        pm.CreateDynamicFrictionAttr().Set(0.5)
        pm.CreateRestitutionAttr().Set(0.4)
        return mat
    return UsdShade.Material(prim)


def fix_physics_material(ctx: _Ctx) -> str:
    """PMT.001 — 충돌 메시에 물리 머티리얼을 purpose=physics 로 바인딩."""
    from pxr import UsdShade
    pmat = _get_or_make_pmat(ctx)
    n = 0
    for prim in _meshes(ctx.stage):
        UsdShade.MaterialBindingAPI.Apply(prim).Bind(
            pmat, bindingStrength=UsdShade.Tokens.weakerThanDescendants, materialPurpose="physics")
        n += 1
    return f"물리 머티리얼(정지마찰 0.6·운동마찰 0.5·반발 0.4)을 충돌 메시 {n}개에 purpose=physics 로 바인딩"


def _tile_of(uv) -> tuple[int, int, int] | None:
    """UV 배열이 쓰는 UDIM 타일 → (타일번호, floor_u, floor_v). 두 타일 이상 걸치면 None."""
    import math
    tiles = {(math.floor(float(u)), math.floor(float(v))) for u, v in uv}
    if len(tiles) != 1:
        return None
    fu, fv = tiles.pop()
    return 1001 + fu + 10 * fv, fu, fv


def de_udim(ctx: _Ctx) -> str:
    """UDIM(<UDIM>) 텍스처를 타일별 concrete 텍스처로 전개 — NP.008/AA.001 의 실제 원인.

    검증기의 리졸버는 `<UDIM>` 을 확장하지 않으므로(PathsExistChecker·AnchoredAssetPathsChecker)
    `./textures/X.<UDIM>.png` 는 "does not resolve to an existing file" 로 **무조건 실패**한다.
    NVIDIA 원본 OEM 자산도 같은 상태다.

    이 자산들은 타일이 **메시 단위로** 갈린다(Hammer: head=1001, handle/shaft=1002 /
    Storage_Rack: 34개=1001, 70개=1002, 두 타일 걸친 메시 0개). 그래서
      1) 타일별로 머티리얼 사본을 만들어 그 타일의 실제 파일명을 박고
      2) 해당 메시를 그 머티리얼에 재바인딩하고
      3) 그 메시의 UV 를 floor 만큼 평행이동해 [0,1) 로 되돌린다
    → 샘플되는 텍셀이 이전과 동일하므로 **룩이 픽셀 단위로 보존**된다(렌더 비교로 검증).
    한 메시가 두 타일을 걸치면 UV 재작업이 필요하므로 건드리지 않고 사유를 남긴다."""
    import numpy as _np
    from pxr import Gf, Sdf, UsdGeom, UsdShade

    stage = ctx.stage
    udim_mats: dict[str, list] = {}
    for shader, attr in _texture_inputs(stage):
        ap = attr.Get()
        if ap and "<UDIM>" in (ap.path or "").upper():
            mat = shader.GetPrim().GetParent()
            udim_mats.setdefault(str(mat.GetPath()), []).append(attr)
    if not udim_mats:
        return ""

    # 머티리얼 → 바인딩된 메시들
    bound: dict[str, list] = {p: [] for p in udim_mats}
    for prim in _meshes(stage):
        b = UsdShade.MaterialBindingAPI(prim).GetDirectBinding()
        mp = str(b.GetMaterialPath()) if b else ""
        if mp in bound:
            bound[mp].append(prim)

    layer = stage.GetRootLayer()
    notes, skipped = [], []
    for mpath, attrs in udim_mats.items():
        groups: dict[int, list] = {}
        shifts: dict[int, tuple[int, int]] = {}
        for prim in bound.get(mpath, []):
            a = prim.GetAttribute("primvars:st")
            if not a or not a.HasAuthoredValue():
                continue
            t = _tile_of(_np.asarray(a.Get(), float))
            if t is None:
                skipped.append(prim.GetName())
                continue
            tile, fu, fv = t
            groups.setdefault(tile, []).append(prim)
            shifts[tile] = (fu, fv)
        if not groups:
            continue
        tiles = sorted(groups)
        # 사본을 **먼저 전부** 만든다. 원본을 먼저 치환하고 나서 복사하면 <UDIM> 이 이미
        # 사라진 상태를 복사해 모든 사본에 같은 타일이 박힌다(실측 버그).
        targets: dict[int, str] = {}
        for idx, tile in enumerate(tiles):
            if idx == 0:
                targets[tile] = mpath                 # 첫 타일은 원본 머티리얼 재사용
                continue
            tgt_path = f"{mpath}_{tile}"
            if not stage.GetPrimAtPath(tgt_path):
                Sdf.CopySpec(layer, Sdf.Path(mpath), layer, Sdf.Path(tgt_path))
            targets[tile] = tgt_path
        for idx, tile in enumerate(tiles):
            tgt_path = targets[tile]
            tgt = UsdShade.Material(stage.GetPrimAtPath(tgt_path))
            # 이 사본의 모든 텍스처 입력에서 <UDIM> → 실제 타일 번호
            for sh_prim in stage.GetPrimAtPath(tgt_path).GetChildren():
                for at in sh_prim.GetAttributes():
                    v = at.Get() if at.GetTypeName() == Sdf.ValueTypeNames.Asset else None
                    if v is None or "<UDIM>" not in (v.path or "").upper():
                        continue
                    at.Set(Sdf.AssetPath(_re.sub(r"<UDIM>", str(tile), v.path, flags=_re.I)))
            fu, fv = shifts[tile]
            for prim in groups[tile]:
                if idx > 0:
                    UsdShade.MaterialBindingAPI(prim).Bind(tgt)
                if fu or fv:
                    a = prim.GetAttribute("primvars:st")
                    uv = _np.asarray(a.Get(), float)
                    uv[:, 0] -= fu
                    uv[:, 1] -= fv
                    a.Set([Gf.Vec2f(float(x), float(y)) for x, y in uv])
            notes.append(f"타일 {tile}: 메시 {len(groups[tile])}개"
                         + (f" (UV -{fu},-{fv} 평행이동)" if (fu or fv) else ""))
    if not notes:
        return ""
    msg = "UDIM 전개 — " + " · ".join(notes)
    if skipped:
        msg += f" · ⚠ 두 타일 걸쳐 건너뜀: {', '.join(sorted(set(skipped)))}"
    return msg


def fix_mdl_anchor(ctx: _Ctx) -> str:
    """AA.001/VM.MDL.001 — MDL sourceAsset(./materials/) + 텍스처(./textures/)를 복사+앵커(파일 실존 보장)."""
    from pxr import Sdf, UsdShade
    stage = ctx.stage
    # <UDIM> 은 리졸브가 안 돼 NP.008/AA.001 을 깨뜨린다 → 먼저 concrete 타일로 전개.
    udim_note = de_udim(ctx)
    matdir = ctx.asset_dir / "materials"
    done, miss = [], []
    try:
        from ...settings import get_settings
        vroot = Path(get_settings().vmaterials_root) if getattr(get_settings(), "vmaterials_root", "") else None
    except Exception:  # noqa: BLE001
        vroot = None
    for prim in stage.Traverse():
        if not prim.IsA(UsdShade.Shader):
            continue
        a = prim.GetAttribute("info:mdl:sourceAsset")
        if not a or not a.IsValid() or a.Get() is None:
            continue
        ap = a.Get()
        raw = (getattr(ap, "resolvedPath", "") or ap.path or "").replace("\\", "/")
        if not raw:
            continue
        base = os.path.basename(raw)
        # 이미 앵커드+실존이면 통과
        if ap.path.startswith("./") and (ctx.asset_dir / ap.path[2:]).exists():
            continue
        src = None
        for cand in (Path(raw), ctx.asset_dir / raw, ctx.asset_dir / ap.path, (vroot / base) if vroot else None):
            if cand and cand.exists():
                src = cand; break
        if src is None:                            # asset_dir 어디든 같은 이름(예: usdz에서 풀린 0/X.mdl) 찾기
            hits = list(ctx.asset_dir.rglob(base))
            src = hits[0] if hits else None
        if src is None and vroot and vroot.exists():
            hits = list(vroot.rglob(base))
            src = hits[0] if hits else None
        if src is None:            # 검색경로 코어 MDL(@OmniPBR.mdl@) → 설치된 Kit/Isaac 에서 찾는다
            for root in _mdl_search_roots():
                cand = root / base
                if cand.exists():
                    src = cand
                    break
                hits = list(root.rglob(base))
                if hits:
                    src = hits[0]
                    break
        if src is None and _is_remote(raw):       # 온라인 MDL → 받아서 ./materials/ 에 묶기
            url = _norm_url(raw)
            matdir.mkdir(parents=True, exist_ok=True)
            if _fetch(url, matdir / base):
                try:                              # MDL 내부 texture_2d("...") 도 같은 폴더 기준으로 수집
                    txt = (matdir / base).read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    txt = ""
                for rel in set(_re.findall(r'texture_2d\(\s*"([^"]+)"', txt)):
                    rc = rel.lstrip("./")
                    _fetch(url.rsplit("/", 1)[0] + "/" + rc, matdir / rc)
                a.Set(Sdf.AssetPath(f"./materials/{base}"))
                done.append(base)
                continue
        if src is None:
            miss.append(base); continue
        matdir.mkdir(parents=True, exist_ok=True)
        _copy_mdl_closure(src, matdir)          # 본체 + 상대 import 형제 모듈까지(룩 유지)
        if not (matdir / base).exists():
            miss.append(base)
            continue
        a.Set(Sdf.AssetPath(f"./materials/{base}"))
        done.append(base)
    # 텍스처(asset 입력)도 ./textures/ 로 복사+앵커(AA.001) — 실제 OEM 에셋은 텍스처가 있음(SPEC v2 §4.6a)
    texdir = ctx.asset_dir / "textures"
    tdone, tmiss = [], []
    for _shader, attr in _texture_inputs(stage):
        ap = attr.Get()
        raw = (getattr(ap, "resolvedPath", "") or ap.path or "").replace("\\", "/")
        if not raw:
            continue
        base = os.path.basename(raw)
        if ap.path.startswith("./") and (ctx.asset_dir / ap.path[2:]).exists():
            continue
        tsrc = None
        for cand in (Path(raw), ctx.asset_dir / raw, ctx.asset_dir / ap.path):
            if cand and cand.exists():
                tsrc = cand; break
        if tsrc is None:                           # asset_dir 어디든 같은 이름 텍스처 찾기
            hits = list(ctx.asset_dir.rglob(base))
            tsrc = hits[0] if hits else None
        if tsrc is None and _is_remote(raw):       # 온라인 텍스처 → 받아서 ./textures/ 에 묶기
            texdir.mkdir(parents=True, exist_ok=True)
            if _fetch(_norm_url(raw), texdir / base):
                attr.Set(Sdf.AssetPath(f"./textures/{base}"))
                tdone.append(base)
            else:
                tmiss.append(base)
            continue
        if tsrc is None:
            tmiss.append(base); continue
        texdir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(tsrc, texdir / base)
        attr.Set(Sdf.AssetPath(f"./textures/{base}"))
        tdone.append(base)

    # AA.001: 비앵커 자기참조 메타(assetInfo:identifier = @ControlBox.usd@ 등)도 정리.
    # ExtractExternalReferences 가 이를 비앵커 reference 로 잡아 AA.001 을 fail 시킨다(MDL/텍스처와 별개).
    ai_cleaned = 0
    for prim in stage.Traverse():
        try:
            ai = prim.GetAssetInfo()
        except Exception:  # noqa: BLE001
            ai = None
        if not ai or "identifier" not in ai:
            continue
        idv = ai["identifier"]
        ipath = idv.path if hasattr(idv, "path") else str(idv)
        if ipath and not ipath.startswith("./") and not ipath.startswith("../"):
            ai2 = dict(ai)
            ai2.pop("identifier", None)
            prim.SetAssetInfo(ai2)
            ai_cleaned += 1

    parts = []
    if udim_note:
        parts.append(udim_note)
    if ai_cleaned:
        parts.append(f"비앵커 assetInfo identifier {ai_cleaned}개 제거")
    if done:
        parts.append(f"MDL {len(done)}개 ./materials/ 복사+앵커")
    if tdone:
        parts.append(f"텍스처 {len(tdone)}개 ./textures/ 복사+앵커")
    if miss:
        parts.append(f"⚠ 못 찾은 MDL: {', '.join(sorted(set(miss)))}")
    if tmiss:
        parts.append(f"⚠ 못 찾은 텍스처: {', '.join(sorted(set(tmiss)))}")
    return "; ".join(parts) or "앵커할 외부 자산 없음(이미 자기완결)"


def fix_texture_colorspace(ctx: _Ctx) -> str:
    """VM.TEX.002 — albedo/color 류는 sRGB, 그 외(normal/roughness/metallic/…)는 raw 로 colorspace 설정."""
    n_srgb = n_raw = 0
    for _shader, attr in _texture_inputs(ctx.stage):
        srgb = _is_albedo_input(attr.GetName())
        attr.SetColorSpace("sRGB" if srgb else "raw")
        if srgb:
            n_srgb += 1
        else:
            n_raw += 1
    if n_srgb + n_raw == 0:
        return "텍스처 입력 없음"
    return f"컬러스페이스 설정 — sRGB {n_srgb}개(albedo/color), raw {n_raw}개(normal/roughness/etc)"


def fix_texture_size(ctx: _Ctx) -> str:
    """VM.TEX.001 — 16384px 초과 텍스처를 비율 유지 다운스케일(Pillow). 번들된 ./textures/ 파일 대상."""
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001
        return "⚠ Pillow 없음 — 텍스처 크기 보정 불가"
    LIM = 16384
    fixed = []
    for _shader, attr in _texture_inputs(ctx.stage):
        ap = attr.Get()
        p = ap.path
        fpath = (ctx.asset_dir / p[2:]) if p.startswith("./") else Path(getattr(ap, "resolvedPath", "") or p)
        if not fpath.exists():
            continue
        try:
            with Image.open(fpath) as im:
                w, h = im.size
                if w <= LIM and h <= LIM:
                    continue
                sc = LIM / max(w, h)
                im2 = im.resize((max(1, int(w * sc)), max(1, int(h * sc))), Image.LANCZOS)
                im2.save(fpath)
                fixed.append(f"{fpath.name} {w}×{h}→{im2.size[0]}×{im2.size[1]}")
        except Exception:  # noqa: BLE001
            continue
    return ("다운스케일: " + ", ".join(fixed)) if fixed else "16384px 초과 텍스처 없음"


def fix_material_bind(ctx: _Ctx) -> str:
    """VM.MAT.001 — 머티리얼 없는 모든 GPrim 에 머티리얼 바인딩(그래스프 커브 포함)."""
    from pxr import UsdShade
    mats = _materials(ctx.stage)
    if not mats:
        return "⚠ 바인딩할 머티리얼이 없습니다 — 먼저 재질 추론(material-usd) 카드를 거치세요."
    mat = mats[0]
    n = 0
    for prim in _gprims(ctx.stage):
        bapi = UsdShade.MaterialBindingAPI(prim)
        if not bapi.GetDirectBinding().GetMaterial():
            UsdShade.MaterialBindingAPI.Apply(prim).Bind(mat)
            n += 1
    return f"머티리얼 미바인딩 GPrim {n}개에 '{mat.GetPrim().GetName()}' 바인딩" if n else "모든 GPrim 이미 바인딩됨"


def fix_grasp(ctx: _Ctx) -> str:
    """GSP.001 — grasp_identifier* BasisCurves(≥2점) 추가 + 머티리얼 바인딩."""
    from pxr import Gf, UsdGeom, UsdShade
    stage = ctx.stage
    base = stage.GetDefaultPrim().GetPath().pathString if (stage.GetDefaultPrim() and stage.GetDefaultPrim().IsValid()) else ""
    path = f"{base}/grasp_identifier_curve"
    if stage.GetPrimAtPath(path) and stage.GetPrimAtPath(path).IsValid():
        return "그래스프 커브 이미 존재"
    lo, hi = _world_bbox(stage)
    pts_cfg = ctx.cfg.get("grasp_points")
    placeholder = not pts_cfg
    if pts_cfg and len(pts_cfg) >= 2:
        pts = [Gf.Vec3f(*[float(x) for x in p]) for p in pts_cfg[:2]]
    else:  # bbox 중간 높이 가로지르는 placeholder
        zc = (lo[2] + hi[2]) / 2; yc = (lo[1] + hi[1]) / 2
        pts = [Gf.Vec3f(lo[0], yc, zc), Gf.Vec3f(hi[0], yc, zc)]
    curve = UsdGeom.BasisCurves.Define(stage, path)
    curve.CreateTypeAttr(UsdGeom.Tokens.linear)
    curve.CreatePointsAttr(pts)
    curve.CreateCurveVertexCountsAttr([2])
    w = max(1e-3, (hi[0] - lo[0]) * 0.01)
    curve.CreateWidthsAttr([w, w]); curve.SetWidthsInterpolation(UsdGeom.Tokens.vertex)
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]; zs = [p[2] for p in pts]
    curve.CreateExtentAttr([Gf.Vec3f(min(xs), min(ys), min(zs)), Gf.Vec3f(max(xs), max(ys), max(zs))])
    mats = _materials(stage)
    if mats:
        UsdShade.MaterialBindingAPI.Apply(curve.GetPrim()).Bind(mats[0])
    tag = " (placeholder — 실제 파지 위치로 교체 권장)" if placeholder else ""
    coords = " → ".join(f"({p[0]:.3f}, {p[1]:.3f}, {p[2]:.3f})" for p in pts)
    return f"그래스프 커브(grasp_identifier_curve) 추가: {coords} m · {len(pts)}점{tag}"


def fix_metadata(ctx: _Ctx) -> str:
    """NP.006/SR.001 — customLayerData 필수 키."""
    rl = ctx.stage.GetRootLayer()
    cfg = ctx.cfg
    name = cfg.get("package_name") or "asset"
    kind = cfg.get("asset_kind") or "prop"
    date = datetime.date.today().isoformat()
    cld = dict(rl.customLayerData)
    cld.update({
        "asset_name": name,
        "asset_type": kind,
        "source_file": cfg.get("source_file") or ctx.usd_name,
        "usd_date_generated": date,
        "SimReady_Metadata": {
            "asset_type": kind,
            "validation": {"profile": cfg.get("profile", "Prop-Robotics-Physx"),
                           "profile_version": cfg.get("version", "1.0.0")},
        },
    })
    rl.customLayerData = cld
    return "customLayerData(asset_name/type/source_file/date/SimReady_Metadata) 설정"


# code → (라벨, 함수). 여러 코드가 한 함수를 공유.
_FN: dict[str, tuple[str, Callable[[_Ctx], str]]] = {
    "UN.001": ("스테이지 단위/축", fix_stage_units), "UN.002": ("스테이지 단위/축", fix_stage_units),
    "UN.005": ("스테이지 단위/축", fix_stage_units), "UN.006": ("스테이지 단위/축", fix_stage_units),
    "UN.007": ("단위 변환(m)", fix_stage_units), "HI.004": ("기본 프림(defaultPrim)", fix_stage_units),
    "COL.001": ("PhysX sdf 충돌", fix_sdf),
    "RB.MB.001": ("멀티바디(강체≥2)", fix_rigidbody), "RB.001": ("강체 코어", fix_rigidbody),
    "RB.007": ("강체 질량", fix_rigidbody),
    "PMT.001": ("물리 머티리얼 바인딩", fix_physics_material),
    "AA.001": ("MDL/텍스처 앵커·복사", fix_mdl_anchor), "VM.MDL.001": ("MDL/텍스처 앵커·복사", fix_mdl_anchor),
    "VM.TEX.001": ("텍스처 다운스케일", fix_texture_size),
    "VM.TEX.002": ("텍스처 컬러스페이스", fix_texture_colorspace),
    "VM.MAT.001": ("머티리얼 바인딩", fix_material_bind),
    "GSP.001": ("그래스프 벡터", fix_grasp),
    "NP.006": ("메타데이터", fix_metadata), "SR.001": ("메타데이터", fix_metadata),
}


def fixable(code: str) -> bool:
    return code in _FN


def apply_fixes(usd_path: Path, codes: list[str], cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """작업본 USD 를 열어 codes 의 수정을 (정해진 순서로, 함수 중복 제거) 적용하고 저장.
    반환: [{codes:[...], label, explanation}] (적용 순서)."""
    from pxr import Usd
    stage = Usd.Stage.Open(str(usd_path))
    if stage is None:
        raise RuntimeError("작업본 USD 를 열지 못했습니다.")
    ctx = _Ctx(stage, usd_path.parent, usd_path.name, cfg)

    # codes 가 문자열이면 글자 단위 순회되는 함정 방지: "all"=전체, 그 외=단일 코드.
    if isinstance(codes, str):
        codes = list(_FN.keys()) if codes == "all" else [codes]
    req = [c for c in codes if c in _FN]
    # 정해진 순서로 정렬(미등록은 뒤)
    req.sort(key=lambda c: (FIX_ORDER.index(c) if c in FIX_ORDER else 99, c))
    results: list[dict[str, Any]] = []
    ran_fns: set[int] = set()
    for c in req:
        label, fn = _FN[c]
        if id(fn) in ran_fns:  # 같은 함수 공유 코드는 한 번만
            # 이미 실행된 함수의 결과에 코드 추가
            for r in results:
                if r["_fnid"] == id(fn):
                    r["codes"].append(c)
            continue
        try:
            expl = fn(ctx)
        except Exception as e:  # noqa: BLE001
            expl = f"⚠ 수정 실패: {e}"
        ran_fns.add(id(fn))
        results.append({"codes": [c], "label": label, "explanation": expl, "_fnid": id(fn)})

    stage.GetRootLayer().Export(str(usd_path))
    for r in results:
        r.pop("_fnid", None)
    return results


def enrich_physics(usd_path: Path, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """검증기가 '내용 없음 → 위반 없음'으로 통과시킨 항목을 실제로 보강한다.
    모든 메시에 충돌체(sdf)+강체+질량+물리 머티리얼을 넣고, 머티리얼/그래스프를 바인딩 →
    내용 없는 에셋을 실제 PhysX 프롭으로. 질량 기본 1kg(정확한 물성은 물성 카드 권장)."""
    from pxr import Usd
    stage = Usd.Stage.Open(str(usd_path))
    if stage is None:
        raise RuntimeError("작업본 USD 를 열지 못했습니다.")
    ctx = _Ctx(stage, usd_path.parent, usd_path.name, cfg)
    results = []
    for label, fn in (("강체+질량", fix_rigidbody), ("충돌체 sdf", fix_sdf),
                      ("물리 머티리얼", fix_physics_material), ("머티리얼 바인딩", fix_material_bind),
                      ("그래스프", fix_grasp)):
        try:
            expl = fn(ctx)
        except Exception as e:  # noqa: BLE001
            expl = f"⚠ {e}"
        results.append({"codes": [], "label": label, "explanation": expl})
    stage.GetRootLayer().Export(str(usd_path))
    return results
