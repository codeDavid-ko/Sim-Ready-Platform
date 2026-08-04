"""Isaac Sim 6.0 헤드리스 RTX **턴테이블 영상** 렌더.

USD 를 받아 (정적 카메라 + 객체/조인트 시간샘플 애니 + 렌즈줌)으로 PNG 시퀀스를 렌더하고
ffmpeg 로 mp4 stitch 한다. 참고: Downloads/Capture_frames_/turntable_render_참고로직.py
및 기존 scripts/isaac_render.py(boot/light/camera/capture 패턴).

핵심(필수):
  - 모터(drive) 조인트는 **물리 제거 전에** 수집.
  - **물리 제거 + Fabric Scene Delegate off** (안 하면 USD 트랜스폼 애니가 렌더에 안 나옴).
  - 카메라는 **정적 + FocalLength(렌즈) 줌** (카메라 이동 줌 금지 — 헤드리스 검은 프레임).
  - 프레임마다 타임라인 시각 진행 + **settle**(RTX 수렴) 후 capture.

실행(Windows):
  python.bat scripts/isaac_turntable.py --usd <abs.usd> --out <dir>
     --turns 1 --spin-dir 1 --seconds 12 --fps 24 --res 1080
     [--zoom --zoom-mult 2.0] [--motors --motor-mode hold|cycle --motor-deg 90]
     --settle 18 --mp4 <out.mp4> --ffmpeg <ffmpeg.exe>
"""

import argparse
import math
import os
import sys

p = argparse.ArgumentParser()
p.add_argument("--usd", required=True)
p.add_argument("--out", required=True)
p.add_argument("--turns", type=float, default=1.0)
p.add_argument("--spin-dir", type=int, default=1)        # +1 CCW / -1 CW
p.add_argument("--seconds", type=float, default=12.0)
p.add_argument("--fps", type=int, default=24)
p.add_argument("--res", type=int, default=1080)
p.add_argument("--zoom", action="store_true")
p.add_argument("--zoom-mult", type=float, default=2.0)
p.add_argument("--zoom-in", type=float, default=0.46, help="줌인 시점(영상 진행 0~1, 이 지점 중심으로 당김)")
p.add_argument("--zoom-out", type=float, default=0.66, help="줌아웃 시점(영상 진행 0~1, 이 지점 중심으로 풂)")
p.add_argument("--motors", action="store_true")
p.add_argument("--motor-mode", default="hold")           # hold | cycle
p.add_argument("--motor-deg", type=float, default=90.0)
p.add_argument("--settle", type=int, default=18)
p.add_argument("--clean", action="store_true", help="거대 환경평면 자동 제거(재질 유지)")
p.add_argument("--motor-targets", default="", help='JSON {prim_path: deg} — 조인트별 구동각 override')
p.add_argument("--mp4", default="")
p.add_argument("--ffmpeg", default="")
p.add_argument("--quality", default="standard", choices=["standard", "hq", "pt"],
               help="standard=RTX 실시간 / hq=RTX+DLSS·반사·AO(GUI수준) / pt=PathTracing(최고화질·느림)")
p.add_argument("--ptspp", type=int, default=64, help="PathTracing 프레임당 누적 샘플 수")
args = p.parse_args()
if args.quality == "pt":
    args.settle = max(args.settle, args.ptspp)   # PT는 settle 업데이트마다 샘플 누적

import json as _json
_MOTOR_TARGETS = {}
if args.motor_targets:
    try:
        _MOTOR_TARGETS = _json.loads(args.motor_targets)
    except Exception:
        _MOTOR_TARGETS = {}
os.makedirs(args.out, exist_ok=True)

# 줌 시점 정규화: 0~1 클램프, 줌아웃은 줌인보다 뒤(최소 한 램프폭 이상), 램프 반폭 _ZR.
_ZR = 0.04
_ZIN = min(max(float(args.zoom_in), 0.0), 1.0)
_ZOUT = min(max(float(args.zoom_out), 0.0), 1.0)
if _ZOUT <= _ZIN:                       # 줌아웃이 줌인보다 앞이면 보정(줌인 후 한 램프 뒤로)
    _ZOUT = min(1.0, _ZIN + 2 * _ZR)


def log(*a):
    print("[isaac_turntable]", *a, flush=True)


def phase(name):
    """현재 단계를 <out>/_phase.txt 에 기록 — 행/타임아웃 시 어디서 막혔는지 추적용."""
    try:
        with open(os.path.join(args.out, "_phase.txt"), "w", encoding="utf-8") as f:
            f.write(name)
    except Exception:
        pass
    log("PHASE", name)


from isaacsim import SimulationApp  # noqa: E402

simulation_app = SimulationApp(
    {"headless": True, "width": args.res, "height": args.res,
     "renderer": ("PathTracing" if args.quality == "pt" else "RaytracedLighting")}
)

import carb  # noqa: E402

# ── 화질 옵션(quality): standard / hq / pt ──
def _apply_quality():
    s = carb.settings.get_settings()
    def _set(k, v):
        try: s.set(k, v)
        except Exception as e: log("quality_set_warn", k, str(e)[:60])
    if args.quality == "hq":   # GUI 수준 RTX 실시간: DLSS + 반사/AO/간접광 + 톤매핑
        _set("/rtx/post/aa/op", 3); _set("/rtx/post/dlss/execMode", 2)
        _set("/rtx/reflections/enabled", True); _set("/rtx/ambientOcclusion/enabled", True)
        _set("/rtx/indirectDiffuse/enabled", True); _set("/rtx/directLighting/sampledLighting/enabled", True)
        _set("/rtx/raytracing/lightcache/cachedLighting/enabled", True); _set("/rtx/translucency/enabled", True)
        _set("/rtx/post/histogram/enabled", True); _set("/rtx/post/tonemap/op", 1)
        log("quality=hq (RTX 실시간 GUI수준)")
    elif args.quality == "pt":  # PathTracing 최고화질(settle 누적으로 수렴)
        _set("/rtx/rendermode", "PathTracing")
        _set("/rtx/pathtracing/spp", 1); _set("/rtx/pathtracing/totalSpp", args.ptspp)
        _set("/rtx/pathtracing/maxBounces", 8); _set("/rtx/pathtracing/maxSpecularAndTransmissionBounces", 8)
        _set("/rtx/pathtracing/clampSpp", 0); _set("/rtx/pathtracing/cached/enabled", True)
        _set("/rtx/post/aa/op", 1); _set("/rtx/post/tonemap/op", 1)
        log("quality=pt (PathTracing totalSpp=%d settle=%d)" % (args.ptspp, args.settle))
    else:
        log("quality=standard (RTX 실시간)")
try:
    _apply_quality()
except Exception as e:
    log("quality_warn", str(e)[:120])
import omni.usd  # noqa: E402
import omni.timeline  # noqa: E402
from pxr import Gf, Usd, UsdGeom, UsdPhysics  # noqa: E402
from isaacsim.core.experimental.utils.stage import define_prim  # noqa: E402
from omni.kit.viewport.utility import capture_viewport_to_file, get_active_viewport  # noqa: E402
from omni.kit.viewport.utility.camera_state import ViewportCameraState  # noqa: E402

# Fabric Scene Delegate off (애니가 렌더에 반영되게) — 부팅 직후.
try:
    carb.settings.get_settings().set("/app/useFabricSceneDelegate", False)
except Exception as e:
    log("fabric_off_warn", str(e)[:100])


def smoothstep(t, a, b):
    if t <= a:
        return 0.0
    if t >= b:
        return 1.0
    x = (t - a) / (b - a)
    return x * x * (3 - 2 * x)


# ---------- (B) 모터 조인트 자동 감지 (물리 제거 전) ----------
def strip_env_planes_inplace(s):
    """환경/바닥 평면(평평 + 실제 객체보다 큼)을 비활성화. 재질/조인트는 유지.
    카메라는 bbox 기준이라 평면만 빼면 절대 스케일과 무관하게 객체가 제대로 프레이밍됨.

    기준 크기는 '평평하지 않은(=실제 객체)' 메시 중앙값 — 메시가 객체1+바닥1뿐일 때
    중앙값이 바닥 쪽으로 쏠려 안 걸리던 문제를 막는다(물리 ground plane 10×10 등도 제거)."""
    bcache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    infos = []  # (prim, mx_dim, mn_dim, diag)
    for prim in s.Traverse():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        try:
            rng = bcache.ComputeWorldBound(prim).ComputeAlignedRange()
        except Exception:
            continue
        if rng.IsEmpty():
            continue
        sz = rng.GetSize()
        mx = max(sz[0], sz[1], sz[2]); mn = min(sz[0], sz[1], sz[2])
        infos.append((prim, mx, mn, rng.GetSize().GetLength()))
    if not infos:
        return

    def _flat(mx, mn):
        return mx > 0 and mn <= mx * 0.02

    nonflat = sorted(diag for _p, mx, mn, diag in infos if not _flat(mx, mn))
    alld = sorted(diag for _p, _mx, _mn, diag in infos)
    ref = (nonflat[len(nonflat) // 2] if nonflat else alld[len(alld) // 2]) or 0.0
    for prim, mx, mn, diag in infos:
        huge = mx > 100.0 or (ref > 0 and diag > ref * 3.0)   # 절대(>100) 또는 객체의 3배↑
        if _flat(mx, mn) and huge:
            prim.SetActive(False)
            log("stripped env plane", prim.GetName(), "diag", round(float(diag), 2), "ref", round(float(ref), 2))


def collect_motorized_joints(s, default_deg=90.0, targets=None):
    targets = targets or {}
    out = []
    xfc = UsdGeom.XformCache()
    for prim in s.Traverse():
        if not prim.IsA(UsdPhysics.Joint):
            continue
        # fixed-base 구조의 world→base_link FixedJoint 를 '문'으로 오인 금지(설계문서 §7).
        # 문 = drive 달린 revolute/prismatic 만.
        if prim.IsA(UsdPhysics.FixedJoint):
            continue
        if not (prim.IsA(UsdPhysics.RevoluteJoint) or prim.IsA(UsdPhysics.PrismaticJoint)):
            continue
        has_drive = True
        if hasattr(UsdPhysics, "DriveAPI"):
            try:
                has_drive = any(
                    prim.HasAPI(UsdPhysics.DriveAPI, tok)
                    for tok in ("angular", "linear", "rotX", "transX")
                )
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
        ovr = targets.get(str(prim.GetPath()))  # 사용자 지정 각도(범위) override
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
        out.append(dict(body=body, Wb=Wb, Wp=Wp, hinge=hinge, axis_w=axis_w,
                        target=float(target), is_pris=is_pris))
    return out


def joint_local_matrix(jt, amount):
    if jt["is_pris"]:
        Mw = Gf.Matrix4d().SetTranslate(jt["axis_w"] * amount)
    else:
        h, ax = jt["hinge"], jt["axis_w"]
        Mw = (Gf.Matrix4d().SetTranslate(-h)
              * Gf.Matrix4d().SetRotate(Gf.Rotation(ax, amount))
              * Gf.Matrix4d().SetTranslate(h))
    return (jt["Wb"] * Mw) * jt["Wp"].GetInverse()


# ---------- (A) 물리 제거 + Fabric off ----------
def strip_physics(s):
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


ctx = omni.usd.get_context()
phase("opening_stage")
log("opening", args.usd)
ctx.open_stage(args.usd)
for _ in range(20):
    simulation_app.update()
stage = ctx.get_stage()
phase("stage_opened")

# 입력 자동 정리(옵션): 거대 환경평면 제거 — bbox/프레이밍 정상화. 재질·조인트 유지.
if args.clean:
    phase("cleaning")
    strip_env_planes_inplace(stage)
    for _ in range(5):
        simulation_app.update()

END = max(1, int(round(args.fps * args.seconds)))
up_tok = str(UsdGeom.GetStageUpAxis(stage))
up = Gf.Vec3d(0, 1, 0) if up_tok == "Y" else Gf.Vec3d(0, 0, 1)

# 모터 정보는 물리 제거 전에
phase("collect_joints")
joints = collect_motorized_joints(stage, default_deg=args.motor_deg, targets=_MOTOR_TARGETS) if args.motors else []
log("motor joints", len(joints))
phase("strip_physics")
strip_physics(stage)

stage.SetStartTimeCode(0)
stage.SetEndTimeCode(END)
stage.SetTimeCodesPerSecond(args.fps)

grp = stage.GetDefaultPrim() or stage.GetPseudoRoot()
cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
rng = cache.ComputeWorldBound(grp).ComputeAlignedRange()
mn, mx = rng.GetMin(), rng.GetMax()
center = Gf.Vec3d((mn + mx) * 0.5)
diag = (mx - mn).GetLength() or 2.0
log("bbox center", tuple(round(c, 2) for c in center), "diag", round(diag, 2))
phase("authoring_animation")

# 턴테이블은 **카메라 궤도(orbit)** 로 한다 — 자산 프림(defaultPrim)을 안 건드리므로
# defaultPrim 유무·배치변환·구조와 무관하게 안정적(검증된 isaac_render 카메라 방식).
# 모터(시간샘플)는 바디 프림에만, 줌(렌즈)·궤도는 프레임 루프에서 직접.

# 모터 구동 키프레임 (옵션) — 바디 프림에 시간샘플(자산 루트는 안 건드림)
for jt in joints:
    bx = UsdGeom.Xformable(jt["body"])
    bx.ClearXformOpOrder()
    bop = bx.AddTransformOp()
    for f in range(END + 1):
        t = f / END
        if args.motor_mode == "cycle":
            amt = jt["target"] * math.sin(smoothstep(t, 0.05, 0.95) * math.pi)
        else:  # hold: 빨리 열고 유지
            amt = jt["target"] * smoothstep(t, 0.04, 0.16)
        bop.Set(joint_local_matrix(jt, amt), Usd.TimeCode(f))

# 라이트 + 카메라 리그 (/_TtRender)
distant = define_prim("/_TtRender/Distant", "DistantLight")
distant.GetAttribute("inputs:intensity").Set(3000.0)
distant.GetAttribute("inputs:angle").Set(2.0)
UsdGeom.Xformable(distant).AddRotateXYZOp().Set(Gf.Vec3f(-40, 30, 0))
dome = define_prim("/_TtRender/Dome", "DomeLight")
dome.GetAttribute("inputs:intensity").Set(800.0)

BASE_FOCAL = 24.0
radius = diag * 1.9            # 객체가 화면 ~절반 → 줌(×2) 여유
elev = 0.42                    # 살짝 위에서
cam_path = "/_TtRender/Cam"
cam = UsdGeom.Camera.Define(stage, cam_path)
cam.GetClippingRangeAttr().Set(Gf.Vec2f(max(diag * 0.001, 0.01), diag * 50.0))
cam.GetFocalLengthAttr().Set(BASE_FOCAL)
viewport = get_active_viewport()
try:
    viewport.resolution = (args.res, args.res)
except Exception:
    pass
viewport.camera_path = cam_path
for _ in range(20):
    simulation_app.update()
cam_state = ViewportCameraState(cam_path, viewport)
fa = cam.GetFocalLengthAttr()
cx, cy, cz = float(center[0]), float(center[1]), float(center[2])
Rh = radius * 0.9
base_az = math.radians(35.0)

# 타임라인(모터 시간샘플 재생용)
tl = omni.timeline.get_timeline_interface()
try:
    tl.set_looping(False)
    tl.set_auto_update(False)
    tl.stop()
except Exception:
    pass

phase("render_frames")
log("zoom enabled", args.zoom, "mult", args.zoom_mult, "in", round(_ZIN, 3), "out", round(_ZOUT, 3))
saved = []
for f in range(END + 1):
    t = f / END
    az = base_az + args.spin_dir * 2.0 * math.pi * args.turns * t
    if up_tok == "Y":
        pos = Gf.Vec3d(cx + Rh * math.cos(az), cy + radius * elev, cz + Rh * math.sin(az))
    else:
        pos = Gf.Vec3d(cx + Rh * math.cos(az), cy + Rh * math.sin(az), cz + radius * elev)
    if args.zoom:  # 렌즈 줌(카메라 이동 아님) — 줌인/줌아웃 시점은 사용자 지정(_ZIN/_ZOUT 중심, ±_ZR 램프)
        zin = smoothstep(t, _ZIN - _ZR, _ZIN + _ZR)
        zout = smoothstep(t, _ZOUT - _ZR, _ZOUT + _ZR)
        fa.Set(float(BASE_FOCAL * (1.0 + (args.zoom_mult - 1.0) * (zin - zout))))
    if joints:  # 모터 시간샘플 재생
        try:
            tl.set_current_time(t * args.seconds)
        except Exception:
            pass
    cam_state.set_target_world(Gf.Vec3d(cx, cy, cz), True)
    cam_state.set_position_world(pos, True)
    for _ in range(args.settle):
        simulation_app.update()
    fpath = os.path.join(args.out, f"frame_{f:05d}.png")
    capture_viewport_to_file(viewport, fpath)
    for _ in range(4):
        simulation_app.update()
    saved.append(fpath)
    if f % 20 == 0:
        log("frame", f, "/", END)

for _ in range(10):
    simulation_app.update()
nok = len([p for p in saved if os.path.exists(p)])
log("DONE frames", nok, "/", END + 1, "->", args.out)

# ffmpeg → mp4
if args.mp4 and args.ffmpeg and os.path.isfile(args.ffmpeg):
    import subprocess
    cmd = [
        args.ffmpeg, "-y", "-framerate", str(args.fps),
        "-i", os.path.join(args.out, "frame_%05d.png"),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-movflags", "+faststart",
        args.mp4,
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=300)
        log("mp4", os.path.getsize(args.mp4) if os.path.exists(args.mp4) else 0, "->", args.mp4)
    except Exception as e:
        log("mp4_err", str(e)[:160])

simulation_app.close()
sys.exit(0)
