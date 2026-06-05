"""sd-texture 파이프라인 — 로컬 Stable Diffusion 으로 PBR 텍스처 머티리얼 생성.

프롬프트 → WSL 전용 venv(~/sd_texture_venv)의 sd_gen_texture.py 실행 →
albedo/normal/roughness PNG → (1) 타일 GLB 스와치(브라우저 model-viewer 인터랙티브)
+ (2) UsdPreviewSurface USD/USDZ(다운로드·Isaac 렌더). NVIDIA/외부 키 불필요.
"""

from __future__ import annotations

import io
import subprocess
import uuid
from pathlib import Path
from typing import Any

import numpy as np

_DISTRO = "Ubuntu-24.04"
_SD_PY = "~/sd_texture_venv/bin/python"
_BACKEND = Path(__file__).resolve().parents[4]          # .../backend
_RUNS = _BACKEND / "_sd_runs"
_WORKER = _BACKEND / "scripts" / "sd_gen_texture.py"
_TIMEOUT = 900


def _to_wsl(p: Path) -> str:
    s = str(p)
    return f"/mnt/{s[0].lower()}{s[2:].replace(chr(92), '/')}"


def sd_available() -> bool:
    """WSL 에 SD 전용 venv 가 준비돼 있는지."""
    try:
        r = subprocess.run(
            ["wsl.exe", "-d", _DISTRO, "bash", "-lc", "test -x ~/sd_texture_venv/bin/python && echo OK"],
            capture_output=True, text=True, timeout=20,
        )
        return "OK" in (r.stdout or "")
    except Exception:  # noqa: BLE001
        return False


def generate(prompt: str, size: int = 768, steps: int = 4, seed: int = 0) -> Path:
    """SD 워커 실행 → albedo/normal/roughness 가 든 run 디렉터리 반환."""
    rid = uuid.uuid4().hex[:12]
    rundir = _RUNS / rid
    rundir.mkdir(parents=True, exist_ok=True)
    (rundir / "prompt.txt").write_text(prompt, encoding="utf-8")
    cmd = [
        "wsl.exe", "-d", _DISTRO, "bash", "-lc",
        f"{_SD_PY} {_to_wsl(_WORKER)} --prompt-file {_to_wsl(rundir / 'prompt.txt')} "
        f"--out {_to_wsl(rundir)} --size {int(size)} --steps {int(steps)} --seed {int(seed)}",
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"텍스처 생성 시간 초과({_TIMEOUT}s).") from None
    if not (rundir / "albedo.png").exists():
        status = (rundir / "status.txt").read_text(encoding="utf-8", errors="replace") if (rundir / "status.txt").exists() else "NO_STATUS"
        err = (rundir / "error.log").read_text(encoding="utf-8", errors="replace")[-900:] if (rundir / "error.log").exists() else ""
        raise RuntimeError(f"텍스처 생성 실패. status={status}\n{err}")
    return rundir


# ───────────────────────── GLB 스와치 (브라우저 인터랙티브) ─────────────────────────
def build_swatch_glb(rundir: Path, tiles: float = 2.0) -> bytes:
    """생성된 PBR 맵을 입힌 평면(타일링) GLB — model-viewer 로 실제 텍스처를 인터랙티브 표시."""
    import trimesh
    from PIL import Image

    albedo = Image.open(rundir / "albedo.png").convert("RGB")
    verts = np.array([[-1, -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0]], float)
    faces = np.array([[0, 1, 2], [0, 2, 3]], int)
    uv = np.array([[0, 0], [tiles, 0], [tiles, tiles], [0, tiles]], float)
    mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
    mat = trimesh.visual.material.PBRMaterial(
        baseColorTexture=albedo, metallicFactor=0.0, roughnessFactor=1.0,
    )
    try:
        mat.normalTexture = Image.open(rundir / "normal.png").convert("RGB")
    except Exception:  # noqa: BLE001
        pass
    mesh.visual = trimesh.visual.TextureVisuals(uv=uv, material=mat)
    return mesh.export(file_type="glb")


# ───────────────────────── USD (UsdPreviewSurface) ─────────────────────────
def author_usd(rundir: Path, stem: str = "sd_material") -> tuple[bytes | None, bytes | None]:
    """평면에 UsdPreviewSurface(albedo/normal/roughness) 바인딩한 .usda + 자기완결 .usdz.
    실패해도 GLB 경로는 살아있도록 best-effort."""
    try:
        from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, UsdUtils
    except Exception:  # noqa: BLE001
        return None, None
    usda_path = rundir / f"{stem}.usda"
    stage = Usd.Stage.CreateNew(str(usda_path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())

    mesh = UsdGeom.Mesh.Define(stage, "/World/Plane")
    mesh.CreatePointsAttr([Gf.Vec3f(-1, -1, 0), Gf.Vec3f(1, -1, 0), Gf.Vec3f(1, 1, 0), Gf.Vec3f(-1, 1, 0)])
    mesh.CreateFaceVertexCountsAttr([4])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    pv = UsdGeom.PrimvarsAPI(mesh.GetPrim()).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.varying)
    pv.Set([(0, 0), (2, 0), (2, 2), (0, 2)])

    mat = UsdShade.Material.Define(stage, "/World/Looks/SDMat")
    shader = UsdShade.Shader.Define(stage, "/World/Looks/SDMat/Surface")
    shader.CreateIdAttr("UsdPreviewSurface")
    st_reader = UsdShade.Shader.Define(stage, "/World/Looks/SDMat/stReader")
    st_reader.CreateIdAttr("UsdPrimvarReader_float2")
    st_reader.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")

    def _tex(name: str, file: str, channels: str, dst):
        t = UsdShade.Shader.Define(stage, f"/World/Looks/SDMat/{name}")
        t.CreateIdAttr("UsdUVTexture")
        t.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(file)
        t.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(st_reader.ConnectableAPI(), "result")
        t.CreateInput("wrapS", Sdf.ValueTypeNames.Token).Set("repeat")
        t.CreateInput("wrapT", Sdf.ValueTypeNames.Token).Set("repeat")
        out = t.CreateOutput(channels, dst)
        return out

    diff = _tex("albedoTex", "albedo.png", "rgb", Sdf.ValueTypeNames.Float3)
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(diff)
    if (rundir / "roughness.png").exists():
        r = _tex("roughTex", "roughness.png", "r", Sdf.ValueTypeNames.Float)
        shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).ConnectToSource(r)
    if (rundir / "normal.png").exists():
        n = _tex("normalTex", "normal.png", "rgb", Sdf.ValueTypeNames.Float3)
        shader.CreateInput("normal", Sdf.ValueTypeNames.Normal3f).ConnectToSource(n)
    shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)
    mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    UsdShade.MaterialBindingAPI(mesh.GetPrim()).Bind(mat)

    stage.GetRootLayer().Save()
    usda_bytes = usda_path.read_bytes()

    usdz_bytes = None
    try:
        usdz_path = rundir / f"{stem}.usdz"
        UsdUtils.CreateNewUsdzPackage(Sdf.AssetPath(str(usda_path)), str(usdz_path))
        if usdz_path.exists():
            usdz_bytes = usdz_path.read_bytes()
    except Exception:  # noqa: BLE001
        usdz_bytes = None
    return usda_bytes, usdz_bytes
