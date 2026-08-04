"""Isaac Sim GUI 턴테이블 startup 스크립트 — GUI Kit 앱 안에서 `--exec` 로 실행된다.

headless(isaac_turntable.py)와 달리 SimulationApp 이 아니라 '이미 떠 있는 GUI Kit 앱' 안에서 돈다.
하는 일(설계문서 §5):
  1) USD open  2) 거대 환경평면 제거  3) bbox 기준 카메라 자동 프레이밍(궤도 키프레임 author)
  4) 활성 뷰포트를 그 카메라로  5) 풀스크린  6) 타임라인 looping 재생  7) RTX 수렴 후 ready 파일 생성.
백엔드는 ready 를 보고 ffmpeg ddagrab 으로 화면(=UI 포함)을 녹화한다. 종료는 백엔드가 프로세스 트리 kill.

옵션은 환경변수로 받는다: SR_GUI_USD / _SECONDS / _FPS / _TURNS / _SPIN / _READY / _CLEAN.
"""
import json
import math
import os

import carb
import omni.kit.app
import omni.timeline
import omni.usd
from pxr import Gf, Usd, UsdGeom, UsdPhysics

USD = os.environ.get("SR_GUI_USD", "")
SECONDS = float(os.environ.get("SR_GUI_SECONDS", "12"))
FPS = int(os.environ.get("SR_GUI_FPS", "30"))
TURNS = float(os.environ.get("SR_GUI_TURNS", "1"))
SPIN = 1 if int(os.environ.get("SR_GUI_SPIN", "1")) >= 0 else -1
READY = os.environ.get("SR_GUI_READY", "")
CLEAN = os.environ.get("SR_GUI_CLEAN", "1") == "1"
ZOOM = os.environ.get("SR_GUI_ZOOM", "0") == "1"
ZOOM_MULT = float(os.environ.get("SR_GUI_ZOOMMULT", "2"))
ZOOM_R = 0.04                                                       # 줌 램프 반폭
ZOOM_IN = min(max(float(os.environ.get("SR_GUI_ZOOMIN", "0.46")), 0.0), 1.0)
ZOOM_OUT = min(max(float(os.environ.get("SR_GUI_ZOOMOUT", "0.66")), 0.0), 1.0)
if ZOOM_OUT <= ZOOM_IN:
    ZOOM_OUT = min(1.0, ZOOM_IN + 2 * ZOOM_R)
MOTORS = os.environ.get("SR_GUI_MOTORS", "0") == "1"
MOTOR_MODE = os.environ.get("SR_GUI_MOTORMODE", "hold")
MOTOR_DEG = float(os.environ.get("SR_GUI_MOTORDEG", "90"))
try:
    MOTOR_TARGETS = json.loads(os.environ.get("SR_GUI_MOTORTARGETS", "") or "{}")
except Exception:
    MOTOR_TARGETS = {}
END = max(1, int(round(FPS * SECONDS)))


def _smoothstep(t, a, b):
    if b <= a:
        return 0.0
    x = min(1.0, max(0.0, (t - a) / (b - a)))
    return x * x * (3 - 2 * x)


def log(*a):
    print("[gui_tt]", *a, flush=True)


# ── 모터(조인트 자동구동) — isaac_turntable.py 와 동일 로직 이식 ──
def _collect_motorized_joints(s, default_deg=90.0, targets=None):
    targets = targets or {}
    out = []
    xfc = UsdGeom.XformCache()
    for prim in s.Traverse():
        if not prim.IsA(UsdPhysics.Joint):
            continue
        # world→base_link FixedJoint 를 '문'으로 오인 금지(설계문서 §7) — drive 달린 revolute/prismatic 만.
        if prim.IsA(UsdPhysics.FixedJoint):
            continue
        if not (prim.IsA(UsdPhysics.RevoluteJoint) or prim.IsA(UsdPhysics.PrismaticJoint)):
            continue
        has_drive = True
        if hasattr(UsdPhysics, "DriveAPI"):
            try:
                has_drive = any(prim.HasAPI(UsdPhysics.DriveAPI, tok) for tok in ("angular", "linear", "rotX", "transX"))
            except Exception:
                has_drive = True
        if not has_drive:
            continue
        j = UsdPhysics.Joint(prim)
        tgts = j.GetBody1Rel().GetTargets()
        if not tgts:
            continue
        body = s.GetPrimAtPath(tgts[0])
        if not body or not body.IsValid():
            continue
        lp1 = j.GetLocalPos1Attr().Get() or Gf.Vec3d(0)
        r1 = j.GetLocalRot1Attr().Get() or Gf.Quatf(1)
        is_pris = prim.IsA(UsdPhysics.PrismaticJoint)
        axis = "Y"
        if prim.IsA(UsdPhysics.RevoluteJoint):
            axis = UsdPhysics.RevoluteJoint(prim).GetAxisAttr().Get() or "Y"
        elif is_pris:
            axis = UsdPhysics.PrismaticJoint(prim).GetAxisAttr().Get() or "Y"
        target = default_deg
        try:
            if prim.IsA(UsdPhysics.RevoluteJoint):
                lo = UsdPhysics.RevoluteJoint(prim).GetLowerLimitAttr().Get()
                hi = UsdPhysics.RevoluteJoint(prim).GetUpperLimitAttr().Get()
                if lo is not None and hi is not None and hi != lo:
                    target = hi if abs(hi) >= abs(lo) else lo
        except Exception:
            pass
        ovr = targets.get(str(prim.GetPath()))
        if ovr is not None:
            try:
                target = float(ovr)
            except Exception:
                pass
        Wb = xfc.GetLocalToWorldTransform(body)
        Wp = xfc.GetLocalToWorldTransform(body.GetParent())
        av = {"X": Gf.Vec3d(1, 0, 0), "Y": Gf.Vec3d(0, 1, 0), "Z": Gf.Vec3d(0, 0, 1)}.get(axis, Gf.Vec3d(0, 1, 0))
        hinge = Wb.Transform(Gf.Vec3d(lp1))
        ro = Gf.Matrix4d(Wb)
        ro.SetTranslateOnly(Gf.Vec3d(0, 0, 0))
        axis_w = ro.TransformDir(Gf.Rotation(Gf.Quatd(r1)).TransformDir(av))
        out.append(dict(body=body, Wb=Wb, Wp=Wp, hinge=hinge, axis_w=axis_w, target=float(target), is_pris=is_pris))
    return out


def _joint_local_matrix(jt, amount):
    if jt["is_pris"]:
        Mw = Gf.Matrix4d().SetTranslate(jt["axis_w"] * amount)
    else:
        h, ax = jt["hinge"], jt["axis_w"]
        Mw = (Gf.Matrix4d().SetTranslate(-h) * Gf.Matrix4d().SetRotate(Gf.Rotation(ax, amount)) * Gf.Matrix4d().SetTranslate(h))
    return (jt["Wb"] * Mw) * jt["Wp"].GetInverse()


def _strip_physics(s):
    try:
        carb.settings.get_settings().set("/app/useFabricSceneDelegate", False)
    except Exception:
        pass
    for prim in list(s.Traverse()):
        if prim.IsValid() and prim.IsA(UsdPhysics.Scene):
            s.RemovePrim(prim.GetPath())
    try:
        from pxr import PhysxSchema
        extra = [PhysxSchema.PhysxRigidBodyAPI, PhysxSchema.PhysxArticulationAPI]
    except Exception:
        extra = []
    for prim in list(s.Traverse()):
        if not prim.IsValid():
            continue
        for api in [UsdPhysics.RigidBodyAPI, UsdPhysics.CollisionAPI, UsdPhysics.ArticulationRootAPI] + extra:
            try:
                if prim.HasAPI(api):
                    prim.RemoveAPI(api)
            except Exception:
                pass


def _author_motor_keyframes(joints):
    for jt in joints:
        bx = UsdGeom.Xformable(jt["body"])
        bx.ClearXformOpOrder()
        bop = bx.AddTransformOp()
        for f in range(END + 1):
            t = f / END
            if MOTOR_MODE == "cycle":
                amt = jt["target"] * math.sin(_smoothstep(t, 0.05, 0.95) * math.pi)
            else:  # hold: 빨리 열고 유지
                amt = jt["target"] * _smoothstep(t, 0.04, 0.16)
            bop.Set(_joint_local_matrix(jt, amt), Usd.TimeCode(f))


def _strip_env_planes(stage):
    """납작 + 실제 객체보다 큰 환경/바닥 평면 비활성화(isaac_turntable 와 동일 규칙)."""
    bc = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    infos = []
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        try:
            r = bc.ComputeWorldBound(prim).ComputeAlignedRange()
        except Exception:
            continue
        if r.IsEmpty():
            continue
        sz = r.GetSize()
        infos.append((prim, max(sz[0], sz[1], sz[2]), min(sz[0], sz[1], sz[2]), sz.GetLength()))
    if not infos:
        return
    flat = lambda mx, mn: mx > 0 and mn <= mx * 0.02
    nf = sorted(d for _p, mx, mn, d in infos if not flat(mx, mn))
    ad = sorted(d for _p, _mx, _mn, d in infos)
    ref = (nf[len(nf) // 2] if nf else ad[len(ad) // 2]) or 0.0
    for prim, mx, mn, d in infos:
        if flat(mx, mn) and (mx > 100.0 or (ref > 0 and d > ref * 3.0)):
            prim.SetActive(False)
            log("stripped env plane", prim.GetName(), "diag", round(float(d), 2))


def _author_orbit_camera(stage):
    """bbox 기준 궤도 카메라를 키프레임으로 author — 타임라인 재생 시 360°*turns 회전."""
    stage.SetStartTimeCode(0)
    stage.SetEndTimeCode(END)
    stage.SetTimeCodesPerSecond(FPS)

    up_tok = str(UsdGeom.GetStageUpAxis(stage))
    grp = stage.GetDefaultPrim() or stage.GetPseudoRoot()
    bc = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    rng = bc.ComputeWorldBound(grp).ComputeAlignedRange()
    mn, mx = rng.GetMin(), rng.GetMax()
    center = Gf.Vec3d((mn + mx) * 0.5)
    diag = (mx - mn).GetLength() or 2.0
    log("bbox center", tuple(round(c, 2) for c in center), "diag", round(diag, 2))

    # 라이트
    dl = stage.DefinePrim("/_SrGui/Distant", "DistantLight")
    dl.GetAttribute("inputs:intensity").Set(3000.0) if dl.GetAttribute("inputs:intensity") else None
    UsdGeom.Xformable(dl).AddRotateXYZOp().Set(Gf.Vec3f(-40, 30, 0))
    dome = stage.DefinePrim("/_SrGui/Dome", "DomeLight")
    if dome.GetAttribute("inputs:intensity"):
        dome.GetAttribute("inputs:intensity").Set(800.0)

    radius = diag * 1.7
    elev = 0.42
    cx, cy, cz = float(center[0]), float(center[1]), float(center[2])
    Rh = radius * 0.9
    base_az = math.radians(35.0)
    up = Gf.Vec3d(0, 1, 0) if up_tok == "Y" else Gf.Vec3d(0, 0, 1)

    BASE_FOCAL = 24.0
    cam = UsdGeom.Camera.Define(stage, "/_SrGui/Cam")
    cam.GetClippingRangeAttr().Set(Gf.Vec2f(max(diag * 0.001, 0.01), diag * 50.0))
    cam.GetFocalLengthAttr().Set(BASE_FOCAL)
    fa = cam.GetFocalLengthAttr()
    xf = UsdGeom.Xformable(cam)
    xf.ClearXformOpOrder()
    op = xf.AddTransformOp()
    for f in range(END + 1):
        t = f / END
        az = base_az + SPIN * 2.0 * math.pi * TURNS * t
        if up_tok == "Y":
            eye = Gf.Vec3d(cx + Rh * math.cos(az), cy + radius * elev, cz + Rh * math.sin(az))
        else:
            eye = Gf.Vec3d(cx + Rh * math.cos(az), cy + Rh * math.sin(az), cz + radius * elev)
        m = Gf.Matrix4d(1.0)
        m.SetLookAt(eye, center, up)          # world→cam(view)
        op.Set(m.GetInverse(), Usd.TimeCode(f))  # cam prim transform = view 역행렬
        if ZOOM:  # 렌즈 줌(초점거리 키프레임) — 사용자 지정 시점(ZOOM_IN/OUT 중심, ±ZOOM_R 램프)
            zin = _smoothstep(t, ZOOM_IN - ZOOM_R, ZOOM_IN + ZOOM_R)
            zout = _smoothstep(t, ZOOM_OUT - ZOOM_R, ZOOM_OUT + ZOOM_R)
            fa.Set(float(BASE_FOCAL * (1.0 + (ZOOM_MULT - 1.0) * (zin - zout))), Usd.TimeCode(f))
    log("zoom keyframes authored" if ZOOM else "no zoom")
    return "/_SrGui/Cam"


# ───────── 업데이트 이벤트 상태머신(open→author→settle→ready) ─────────
_state = {"phase": "open", "wait": 0}


def _on_update(_e):
    ph = _state["phase"]
    try:
        if ph == "open":
            ctx = omni.usd.get_context()
            log("opening", USD)
            ctx.open_stage(USD)
            _state["phase"], _state["wait"] = "settle_open", 0
        elif ph == "settle_open":
            _state["wait"] += 1
            stage = omni.usd.get_context().get_stage()
            if stage and _state["wait"] >= 30:
                if CLEAN:
                    _strip_env_planes(stage)
                # 모터(조인트 구동): 물리 제거 전에 조인트 수집 → 물리 제거(+Fabric off) → 키프레임.
                joints = _collect_motorized_joints(stage, MOTOR_DEG, MOTOR_TARGETS) if MOTORS else []
                if MOTORS:
                    log("motor joints", len(joints))
                    _strip_physics(stage)   # Fabric off + 물리 제거 → 키프레임 애니가 뷰포트에 반영
                cam_path = _author_orbit_camera(stage)
                if MOTORS and joints:
                    _author_motor_keyframes(joints)
                    log("motor keyframes authored")
                try:
                    from omni.kit.viewport.utility import get_active_viewport
                    vp = get_active_viewport()
                    vp.camera_path = cam_path
                except Exception as e:  # noqa: BLE001
                    log("viewport cam set failed", e)
                try:
                    s = carb.settings.get_settings()
                    s.set("/app/window/fullscreen", True)  # 모니터=Isaac → crop 없이 캡처
                except Exception:
                    pass
                tl = omni.timeline.get_timeline_interface()
                tl.set_looping(True)
                tl.set_start_time(0.0)
                tl.set_end_time(float(SECONDS))
                tl.play()
                log("playing, settling for RTX convergence")
                _state["phase"], _state["wait"] = "settle_rtx", 0
        elif ph == "settle_rtx":
            _state["wait"] += 1
            if _state["wait"] >= 90:           # RTX 수렴 대기(초반 노이즈 방지)
                if READY:
                    try:
                        with open(READY, "w", encoding="utf-8") as f:
                            f.write("ready")
                        log("READY ->", READY)
                    except Exception as e:  # noqa: BLE001
                        log("ready write failed", e)
                _state["phase"] = "running"
        # running: 아무것도 안 함(타임라인 loop). 백엔드가 녹화 후 kill.
    except Exception as e:  # noqa: BLE001
        log("ERROR in update", repr(e))
        _state["phase"] = "running"


_sub = omni.kit.app.get_app().get_update_event_stream().create_subscription_to_pop(_on_update, name="sr_gui_tt")
log("startup armed: USD=%s sec=%s fps=%s turns=%s" % (USD, SECONDS, FPS, TURNS))
