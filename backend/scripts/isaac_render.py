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
parser.add_argument("--settle", type=int, default=50, help="RTX 수렴용 프레임 수/뷰")
parser.add_argument("--mp4", default="", help="주면 view_*.png 를 회전 mp4 로 stitch")
parser.add_argument("--ffmpeg", default="", help="ffmpeg 실행경로(mp4 stitch용)")
parser.add_argument("--fps", type=int, default=20)
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

# Lighting (vMaterials MDL needs light). Add even if USD has its own.
distant = define_prim("/World/_RenderDistant", "DistantLight")
distant.GetAttribute("inputs:intensity").Set(3000.0)
distant.GetAttribute("inputs:angle").Set(2.0)
UsdGeom.Xformable(distant).AddRotateXYZOp().Set(Gf.Vec3f(-40, 30, 0))
dome = define_prim("/World/_RenderDome", "DomeLight")
dome.GetAttribute("inputs:intensity").Set(800.0)

# Bounding box of the asset (default prim).
cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
target = stage.GetDefaultPrim() or stage.GetPseudoRoot()
rng = cache.ComputeWorldBound(target).ComputeAlignedRange()
mn, mx = rng.GetMin(), rng.GetMax()
center = (mn + mx) * 0.5
diag = (mx - mn).GetLength() or 2.0
radius = diag * 0.95
log("bbox center", tuple(round(c, 2) for c in center), "diag", round(diag, 2))

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
elev = 0.45  # 위에서 약간 내려다보기
n = max(1, args.views)
saved = []
for i in range(n):
    ang = 2.0 * math.pi * i / n
    pos = Gf.Vec3d(
        float(center[0] + radius * math.cos(ang)),
        float(center[1] + radius * math.sin(ang)),
        float(center[2] + radius * elev),
    )
    cam_state.set_target_world(Gf.Vec3d(float(center[0]), float(center[1]), float(center[2])), True)
    cam_state.set_position_world(pos, True)
    for _ in range(args.settle):
        simulation_app.update()
    fpath = os.path.join(args.out, f"view_{i}.png")
    capture_viewport_to_file(viewport, fpath)
    for _ in range(12):
        simulation_app.update()
    saved.append(fpath)
    log("captured", fpath)

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
