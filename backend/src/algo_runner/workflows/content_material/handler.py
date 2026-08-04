"""content-material 워크플로우 — NVIDIA content-agents Material 파이프라인을
WSL2에서 오케스트레이션.

Windows 백엔드 → wsl.exe → ~/content-agents/run_one.sh (Warp 멀티뷰 렌더 +
구독 Claude VLM via anthropic_oauth 백엔드) → 재질 바인딩된 USD 회수.

material-usd 워크플로우와 달리, 알고리즘은 NVIDIA content-agents가 수행하고
이 핸들러는 얇은 오케스트레이터다(파일 전달 + WSL 실행 + 결과 회수).
"""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path
from typing import Any

from .. import jobs

_DISTRO = "Ubuntu-24.04"
_RUNS = Path(__file__).resolve().parents[4] / "_ca_runs"  # backend/_ca_runs
_SUPPORTED = {".usd", ".usda", ".usdc", ".usdz"}
_TIMEOUT = 2400  # seconds — 렌더(~12분) + 클러스터 단위 VLM 추론 여유(다부품 자산)


def _to_wsl(p: Path) -> str:
    """C:\\X\\Y -> /mnt/c/X/Y"""
    s = str(p)
    drive = s[0].lower()
    rest = s[2:].replace("\\", "/")
    return f"/mnt/{drive}{rest}"


def run(
    params: dict[str, Any],
    file_bytes: bytes | None = None,
    file_name: str | None = None,
    ctx: Any = None,
) -> dict[str, Any]:
    if not file_bytes:
        raise ValueError("USD 파일을 업로드하세요.")
    ext = Path(file_name or "").suffix.lower()
    if ext not in _SUPPORTED:
        raise ValueError(f"USD 형식만 지원합니다(.usd/.usda/.usdc/.usdz). 받은: {ext or '?'}")

    run_id = uuid.uuid4().hex[:12]
    rundir = _RUNS / run_id
    rundir.mkdir(parents=True, exist_ok=True)
    # run_one.sh 가 입력을 '_card_input.usd'(고정 .usd 이름)로 복사해 연다. usdz(zip)는 .usd 로
    # 이름만 바뀌면 못 열림 → geometry 를 텍스트 .usda 로 평탄화해 넘긴다(버전 무관·복사돼도 열림).
    if ext == ".usdz":
        from pxr import Usd, UsdGeom
        tmp = rundir / "input.usdz"; tmp.write_bytes(file_bytes)
        in_path = rundir / "input.usda"
        try:
            stage = Usd.Stage.Open(str(tmp))
            if stage is None:
                raise RuntimeError("usdz 열기 실패")
            dp = stage.GetDefaultPrim()
            if not (dp and dp.IsValid()):   # defaultPrim 없으면 첫 최상위 prim 으로(렌더 프레이밍용)
                for p in stage.GetPseudoRoot().GetChildren():
                    if p.IsA(UsdGeom.Imageable):
                        stage.SetDefaultPrim(p); break
            stage.Flatten().Export(str(in_path))
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"usdz 입력을 .usda 로 변환하지 못했습니다: {exc}") from None
    else:
        in_path = rundir / f"input{ext}"
        in_path.write_bytes(file_bytes)

    wsl_in = _to_wsl(in_path)
    wsl_out = _to_wsl(rundir)
    cmd = [
        "wsl.exe", "-d", _DISTRO, "bash", "-lc",
        f"bash ~/content-agents/run_one.sh '{wsl_in}' '{wsl_out}'",
    ]
    try:
        jobs.run(cmd, timeout=_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"파이프라인 시간 초과({_TIMEOUT}s).") from None
    except FileNotFoundError:
        raise RuntimeError("wsl.exe 를 찾을 수 없습니다(WSL2 필요).") from None

    status_f = rundir / "status.txt"
    status = status_f.read_text(encoding="utf-8", errors="replace").strip() if status_f.exists() else "NO_STATUS"

    bindings: dict[str, Any] = {}
    bf = rundir / "bindings.json"
    if bf.exists():
        try:
            bindings = json.loads(bf.read_text(encoding="utf-8"))
        except ValueError:
            pass

    log_tail = ""
    lf = rundir / "run.log"
    if lf.exists():
        lines = lf.read_text(encoding="utf-8", errors="replace").splitlines()
        # 텔레메트리/배너 꼬리보다 '진짜 실패 사유'를 우선 추출한다.
        kw = ("failed at task", "blank or near-blank", "Pipeline failed", "Error running pipeline",
              "RuntimeError", "CUDA", "out of memory", "Traceback")
        hits = [ln.strip() for ln in lines if any(k.lower() in ln.lower() for k in kw)]
        # 중복/노이즈 제거 후 의미 있는 사유 몇 줄 + 마지막 5줄
        seen_l: set[str] = set()
        meaningful = []
        for ln in hits:
            base = ln[ln.find("]") + 1:] if "]" in ln[:40] else ln
            base = base.strip("│ ").strip()
            if base and base not in seen_l:
                seen_l.add(base)
                meaningful.append(base)
        parts = meaningful[:6] + (["…"] if meaningful else []) + [ln for ln in lines[-5:]]
        log_tail = "\n".join(parts)

    out_usd = rundir / "output.usd"
    asset = None
    preview = None
    if out_usd.exists() and ctx is not None:
        stem = Path(file_name or "asset").stem
        usd_data = out_usd.read_bytes()
        asset = ctx.register_asset(
            display_name=stem,
            filename=f"{stem}_material.usd",
            data=usd_data,
            meta={
                "source": file_name,
                "engine": "nvidia-content-agents",
                "bindings": bindings.get("bindings", {}),
            },
        )
        # 브라우저 3D 미리보기용 PBR GLB. content-agents 출력은 입력을 서브레이어로
        # 참조해 자기완결이 아니므로, 입력 지오메트리 + 추론 bindings 로 PBR 근사.
        try:
            from ..preview import glb_from_usd_with_bindings

            preview = ctx.register_asset(
                display_name=f"{stem} (preview)",
                filename=f"{stem}_preview.glb",
                data=glb_from_usd_with_bindings(file_bytes, bindings.get("bindings", {})),
                meta={"stage": "preview"},
            )
        except Exception:  # noqa: BLE001 -- preview is best-effort
            preview = None
        # 자기완결 USDZ(형상+MaterialX+텍스처 번들) → Windows Isaac 렌더용.
        usdz = rundir / "output.usdz"
        if usdz.exists():
            try:
                usdz_asset = ctx.register_asset(
                    display_name=f"{stem} (usdz)",
                    filename=f"{stem}.usdz",
                    data=usdz.read_bytes(),
                    meta={"stage": "usdz", "self_contained": True},
                )
            except Exception:  # noqa: BLE001
                usdz_asset = None
        else:
            usdz_asset = None
    else:
        usdz_asset = None
    if asset is None:
        raise RuntimeError(
            f"출력 USD가 생성되지 않았습니다. status={status}.\n로그 마지막:\n{log_tail}"
        )

    return {
        "engine": "NVIDIA content-agents (Material) · Warp 멀티뷰 렌더 · 구독 Claude VLM",
        "status": status,
        "bindings": bindings.get("bindings", {}),
        "asset": asset,
        "preview": preview,
        "usdz_asset": usdz_asset,
        "log_tail": log_tail,
    }
