"""Isaac Sim 6.0 headless multi-angle RTX render of a self-contained USD.

material-usd 출력 USD(자기완결 vMaterials MDL)를 여러 각도에서 RTX로 렌더해
실제 MDL 재질이 보이는 PNG들을 만든다. 플랫폼 카드가 python.bat 로 호출.

실행 (Windows):
  "C:\\Omniverse\\IsaacSim\\IsaacSim\\_build\\windows-x86_64\\release\\python.bat" ^
     backend\\scripts\\isaac_render.py --usd <abs.usda> --out <dir> --views 6 --res 720

검증된 패턴 출처: Desktop\\anymal_video\\replay_scripts (boot/light/camera/capture).
"""

import argparse
import math
import os
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--usd", required=True)
parser.add_argument("--out", required=True)
parser.add_argument("--views", type=int, default=6)
parser.add_argument("--res", type=int, default=720)
parser.add_argument("--lights", default="auto", choices=["auto", "thumbnail"],
                    help="auto=스테이지 라이트 있으면 그걸로·없으면 디폴트 / thumbnail=NVIDIA 리그(RectLight+Dome)")
parser.add_argument("--settle", type=int, default=50, help="RTX 수렴용 프레임 수/뷰")
parser.add_argument("--dome", type=float, default=0.0,
                    help="0 보다 크면 DomeLight(앰비언트)를 이 intensity 로 **보장**한다. "
                         "배경색을 결정하는 것이 Dome 이라, 자산에 Dome 이 없으면 배경이 검게 나오고 "
                         "있으면 회색이 된다 → 여러 자산의 썸네일 배경을 통일하려면 이 값을 준다. "
                         "이미 Dome 이 있으면 추가하지 않는다(이중 노출 방지).")
parser.add_argument("--fit", type=float, default=1.0,
                    help="카메라 거리 배수(1.0=기존). 길쭉한 자산은 기본 거리에서 위아래가 잘리므로 "
                         "1.4~1.6 을 주면 전체가 화면에 들어온다. 조명 리그는 영향 없음.")
parser.add_argument("--mp4", default="", help="주면 view_*.png 를 회전 mp4 로 stitch")
parser.add_argument("--ffmpeg", default="", help="ffmpeg 실행경로(mp4 stitch용)")
parser.add_argument("--fps", type=int, default=20)
parser.add_argument("--elevations", type=int, default=1,
                    help=">1 이면 고도×방위각 격자 렌더(view_<e>_<a>.png). 1 이면 방위각만(view_<i>.png, mp4 가능).")
args = parser.parse_args()

os.makedirs(args.out, exist_ok=True)

from isaacsim import SimulationApp  # noqa: E402

simulation_app = SimulationApp(
    {"headless": True, "width": args.res, "height": args.res, "renderer": "RaytracedLighting"}
)

import omni.usd  # noqa: E402
from pxr import Gf, Usd, UsdGeom  # noqa: E402
from isaacsim.core.experimental.utils.stage import define_prim  # noqa: E402
from omni.kit.viewport.utility import capture_viewport_to_file, get_active_viewport  # noqa: E402
from omni.kit.viewport.utility.camera_state import ViewportCameraState  # noqa: E402


def log(*a):
    print("[isaac_render]", *a, flush=True)


ctx = omni.usd.get_context()
log("opening", args.usd)
ctx.open_stage(args.usd)
for _ in range(20):
    simulation_app.update()
stage = ctx.get_stage()

# Lighting 은 bbox 계산 후 아래에서 설정한다(thumbnail RectLight 가 center/radius 필요).

# Bounding box of the asset — 큰 바닥/환경 평면(물리 ground plane 등)은 제외하고
# 실제 객체 메시 기준으로 프레이밍(아니면 카메라가 평면을 담아 객체가 작게 나옴).
cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
target = stage.GetDefaultPrim() or stage.GetPseudoRoot()


def _aligned(prim):
    try:
        r = cache.ComputeWorldBound(prim).ComputeAlignedRange()
        return None if r.IsEmpty() else r
    except Exception:  # noqa: BLE001
        return None


mesh_ranges = []
for p in stage.Traverse():
    if p.IsA(UsdGeom.Mesh):
        r = _aligned(p)
        if r is not None:
            mesh_ranges.append(r)

def _is_flat(r):
    d = r.GetMax() - r.GetMin()
    dims = [abs(d[0]), abs(d[1]), abs(d[2])]
    return max(dims) > 0 and min(dims) <= max(dims) * 0.02      # 한 축이 납작 = 평면


chosen = list(mesh_ranges)
if len(mesh_ranges) >= 2:
    # 기준 크기는 '납작하지 않은(실제 객체)' 메시 중앙값 — 없으면 전체 중앙값.
    nonflat = [(r.GetMax() - r.GetMin()).GetLength() for r in mesh_ranges if not _is_flat(r)]
    alld = sorted((r.GetMax() - r.GetMin()).GetLength() for r in mesh_ranges)
    ref = sorted(nonflat)[len(nonflat) // 2] if nonflat else (alld[len(alld) // 2] or 0.0)
    keep = []
    for r in mesh_ranges:
        d = r.GetMax() - r.GetMin()
        mx_dim = max(abs(d[0]), abs(d[1]), abs(d[2]))
        diag_r = (r.GetMax() - r.GetMin()).GetLength()
        huge = mx_dim > 100.0 or (ref > 0 and diag_r > ref * 3.0)   # 절대/상대로 비정상 큼
        if _is_flat(r) and huge:
            continue                                                # 바닥/환경 평면 제외
        keep.append(r)
    if keep:
        chosen = keep

agg = Gf.Range3d()
for r in chosen:
    agg.UnionWith(r)
if agg.IsEmpty():  # 메시를 못 찾으면 기존 방식(전체)로 폴백
    agg = cache.ComputeWorldBound(target).ComputeAlignedRange()
mn, mx = agg.GetMin(), agg.GetMax()
center = (mn + mx) * 0.5
diag = (mx - mn).GetLength() or 2.0
radius = diag * 0.95
log("bbox center", tuple(round(c, 2) for c in center), "diag", round(diag, 2),
    "meshes", len(mesh_ranges), "kept", len(chosen))

# Lighting
_LIGHT_TYPES = {"DistantLight", "DomeLight", "SphereLight", "RectLight",
                "DiskLight", "CylinderLight", "GeometryLight", "PortalLight"}
if args.lights == "thumbnail":
    # NVIDIA auto_thumbnail 리그 스타일: 상단 RectLight 키(15000) + Dome 앰비언트 — 밝고 일관.
    dome = define_prim("/World/_RenderDome", "DomeLight")
    dome.GetAttribute("inputs:intensity").Set(1500.0)
    key = define_prim("/World/_RenderKey", "RectLight")
    key.GetAttribute("inputs:intensity").Set(15000.0)
    key.GetAttribute("inputs:width").Set(float(radius * 4))
    key.GetAttribute("inputs:height").Set(float(radius * 4))
    kx = UsdGeom.Xformable(key)
    kx.AddTranslateOp().Set(Gf.Vec3d(float(center[0]), float(center[1]), float(center[2] + radius * 2.5)))
    log("thumbnail 조명 리그: RectLight 15000(상단) + Dome 1500")
else:
    _existing = [p for p in stage.Traverse()
                 if p.GetTypeName() in _LIGHT_TYPES and not str(p.GetPath()).startswith("/World/_Render")]
    if _existing:
        log("스테이지 라이트 사용:", [p.GetName() for p in _existing])
    else:
        distant = define_prim("/World/_RenderDistant", "DistantLight")
        distant.GetAttribute("inputs:intensity").Set(3000.0)
        distant.GetAttribute("inputs:angle").Set(2.0)
        UsdGeom.Xformable(distant).AddRotateXYZOp().Set(Gf.Vec3f(-40, 30, 0))
        dome = define_prim("/World/_RenderDome", "DomeLight")
        dome.GetAttribute("inputs:intensity").Set(800.0)
        log("스테이지 라이트 없음 → 디폴트(Distant+Dome)")

# 배경 통일용 Dome — 배경색은 DomeLight 강도로 정해진다. 자산에 Dome 이 없으면 배경이 검게,
# 있으면 회색으로 나와 썸네일이 들쭉날쭉해진다. --dome 을 주면 **모든 자산이 같은 배경**이 되게
# 강도를 못박는다: 렌더용으로 우리가 넣은 Dome(/World/_Render*)은 강도를 덮어쓰고,
# 자산이 직접 authored 한 Dome 은 룩이므로 건드리지 않는다. 하나도 없으면 새로 만든다.
if args.dome > 0:
    _own, _mine = [], []
    for p in stage.Traverse():
        if p.GetTypeName() != "DomeLight":
            continue
        (_mine if str(p.GetPath()).startswith("/World/_Render") else _own).append(p)
    if _own:
        log(f"자산 authored Dome {len(_own)}개 — 룩이라 강도 유지")
    else:
        if not _mine:
            _mine = [define_prim("/World/_RenderBgDome", "DomeLight")]
        for _d in _mine:
            _d.GetAttribute("inputs:intensity").Set(float(args.dome))
        log(f"배경 통일 Dome {len(_mine)}개 → intensity {args.dome}")

# Render camera with clip range scaled to the scene.
cam_path = "/World/_RenderCam"
cam = UsdGeom.Camera.Define(stage, cam_path)
cam.GetClippingRangeAttr().Set(Gf.Vec2f(max(diag * 0.001, 0.01), diag * 50.0))
cam.GetFocalLengthAttr().Set(24.0)

viewport = get_active_viewport()
try:
    viewport.resolution = (args.res, args.res)
except Exception:
    pass
viewport.camera_path = cam_path
for _ in range(20):
    simulation_app.update()

cam_state = ViewportCameraState(cam_path, viewport)
n = max(1, args.views)
E = max(1, args.elevations)
saved = []


def _capture(pos, fname):
    cam_state.set_target_world(Gf.Vec3d(float(center[0]), float(center[1]), float(center[2])), True)
    cam_state.set_position_world(pos, True)
    for _ in range(args.settle):
        simulation_app.update()
    fpath = os.path.join(args.out, fname)
    capture_viewport_to_file(viewport, fpath)
    for _ in range(12):
        simulation_app.update()
    saved.append(fpath)
    log("captured", fpath)


cam_r = radius * max(0.1, float(args.fit))   # 카메라 배치용 반경(조명은 radius 유지)
if E == 1:
    # 방위각만(기존 동작) — view_<i>.png, mp4 stitch 가능
    elev = 0.45     # 위에서 약간 내려다보기
    base_az = 0.0
    if n == 1:      # 단일 뷰(썸네일) = 대각선 위 히어로 샷(코너에서 두 면 + 더 위에서)
        base_az = math.radians(-45.0)
        elev = 0.6
    for i in range(n):
        ang = 2.0 * math.pi * i / n + base_az
        _capture(Gf.Vec3d(
            float(center[0] + cam_r * math.cos(ang)),
            float(center[1] + cam_r * math.sin(ang)),
            float(center[2] + cam_r * elev),
        ), f"view_{i}.png")
else:
    # 고도×방위각 격자 — view_<e>_<a>.png. 구면좌표라 거리 일정. e=0 이 가장 수평, 커질수록 위에서.
    dist = cam_r * 1.05
    phi_lo, phi_hi = math.radians(15.0), math.radians(65.0)
    for e in range(E):
        phi = phi_lo + (phi_hi - phi_lo) * (e / (E - 1))
        for a in range(n):
            th = 2.0 * math.pi * a / n
            _capture(Gf.Vec3d(
                float(center[0] + dist * math.cos(phi) * math.cos(th)),
                float(center[1] + dist * math.cos(phi) * math.sin(th)),
                float(center[2] + dist * math.sin(phi)),
            ), f"view_{e}_{a}.png")

for _ in range(10):
    simulation_app.update()
log("DONE", len([p for p in saved if os.path.exists(p)]), "images ->", args.out)

# 회전 mp4 stitch (옵션)
if args.mp4 and args.ffmpeg and os.path.isfile(args.ffmpeg):
    import subprocess
    cmd = [
        args.ffmpeg, "-y", "-framerate", str(args.fps),
        "-i", os.path.join(args.out, "view_%d.png"),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        args.mp4,
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=120)
        log("mp4", os.path.getsize(args.mp4) if os.path.exists(args.mp4) else 0, "->", args.mp4)
    except Exception as e:
        log("mp4_err", str(e)[:120])

simulation_app.close()
sys.exit(0)
