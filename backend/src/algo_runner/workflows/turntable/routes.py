"""턴테이블 영상 렌더 카드 — USD 업로드 + 옵션 → Isaac Sim RTX 턴테이블 mp4 (잡).

main 이 prefix=/api/workflows/turntable 로 마운트.
  POST /api/workflows/turntable/render-submit   USD + 옵션 → {job_id}
폴링: GET /api/workflows/jobs/{job_id} → {video: asset}
취소: POST /api/workflows/jobs/{job_id}/cancel (Isaac 프로세스 트리 종료)

회전(기본) + 줌(렌즈)·모터(drive 조인트 자동 감지) 옵션은 셋 다 독립 조합.
실제 렌더 메커니즘: scripts/isaac_turntable.py (물리제거+Fabric off / 시간샘플 애니 / 정적카메라 렌즈줌).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import PurePath
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ...auth import require_auth
from .. import jobs, storage
from ..material_usd import isaac

import json as _json
import tempfile as _tf

router = APIRouter(tags=["turntable"])

_WF_ID = "turntable"
_MAX_FILE = 1024 * 1024 * 1024  # 1GB
_SUPPORTED = {".usd", ".usda", ".usdc", ".usdz"}


def _detect_joints(data: bytes, ext: str) -> list[dict[str, Any]]:
    """Isaac 없이 pxr 로 모터(drive) 달린 조인트 감지 → [{path,name,type,axis,lower,upper}]."""
    from pxr import Usd, UsdPhysics

    fd, path = _tf.mkstemp(suffix=ext if ext in _SUPPORTED else ".usd")
    os.close(fd)
    with open(path, "wb") as f:
        f.write(data)
    out: list[dict[str, Any]] = []
    try:
        stage = Usd.Stage.Open(path)
        for prim in stage.Traverse():
            if not prim.IsA(UsdPhysics.Joint):
                continue
            has_drive = True
            if hasattr(UsdPhysics, "DriveAPI"):
                try:
                    has_drive = any(prim.HasAPI(UsdPhysics.DriveAPI, t) for t in ("angular", "linear", "rotX", "transX"))
                except Exception:
                    has_drive = True
            if not has_drive:
                continue
            jtype, axis, lo, hi = "fixed", "Y", None, None
            if prim.IsA(UsdPhysics.RevoluteJoint):
                jtype = "revolute"
                rj = UsdPhysics.RevoluteJoint(prim)
                axis = rj.GetAxisAttr().Get() or "Y"
                lo, hi = rj.GetLowerLimitAttr().Get(), rj.GetUpperLimitAttr().Get()
            elif prim.IsA(UsdPhysics.PrismaticJoint):
                jtype = "prismatic"
                pj = UsdPhysics.PrismaticJoint(prim)
                axis = pj.GetAxisAttr().Get() or "Y"
                lo, hi = pj.GetLowerLimitAttr().Get(), pj.GetUpperLimitAttr().Get()
            out.append({
                "path": str(prim.GetPath()), "name": prim.GetName(),
                "type": jtype, "axis": str(axis),
                "lower": (float(lo) if lo is not None else None),
                "upper": (float(hi) if hi is not None else None),
            })
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return out


def _analyze_refs(path: str, ext: str) -> tuple[list[str], list[str]]:
    """외부 참조(레퍼런스/페이로드/서브레이어) 분석 → (warnings, notes).
    누락(서버에서 못 찾음) 참조가 있으면 '함께 올리거나 usdz 로 묶어라' 안내."""
    from pxr import UsdUtils
    warns: list[str] = []
    notes: list[str] = []
    if ext == ".usdz":
        return warns, notes   # usdz 는 자기완결 패키지 — 검사 불필요
    try:
        layers, assets, unresolved = UsdUtils.ComputeAllDependencies(path)
    except Exception:  # noqa: BLE001
        return warns, notes
    root_id = path.replace("\\", "/").lower()
    ext_layers = [l.identifier for l in layers if l and l.identifier.replace("\\", "/").lower() != root_id]
    ext_count = len(ext_layers) + len(list(assets))
    unres = [str(u) for u in (unresolved or [])]
    remote_unres = [u for u in unres if u.lower().startswith(("http", "omniverse", "s3"))]
    local_unres = [u for u in unres if u not in remote_unres]

    if local_unres:
        head = ", ".join(os.path.basename(u) or u for u in local_unres[:6])
        warns.append(
            f"이 USD가 참조하는 외부 파일 {len(local_unres)}개를 찾지 못했습니다(렌더 시 빠짐): {head}"
            + (" 외…" if len(local_unres) > 6 else "")
            + " — 참조 파일을 함께 두거나, 올리는 PC에서 .usdz 로 묶어 올리세요(참조가 한 파일에 포함됨)."
        )
    elif ext_count > 0:
        notes.append(
            f"외부 파일 {ext_count}개를 참조합니다(이 PC에선 해석됨). 다른 PC에서도 쓰려면 .usdz 로 묶어 올리는 걸 권장합니다."
        )
    if remote_unres:
        notes.append(f"원격(온라인) 참조 {len(remote_unres)}개 — 네트워크로 받아오며 첫 렌더가 느릴 수 있습니다.")
    return warns, notes


def _inspect(data: bytes, ext: str) -> dict[str, Any]:
    """렌더 전 빠른 사전 검사(Isaac 없이 pxr) — 문제/유의점을 UI에 미리 알리기 위함."""
    from pxr import Usd, UsdGeom

    fd, path = _tf.mkstemp(suffix=ext if ext in _SUPPORTED else ".usd")
    os.close(fd)
    with open(path, "wb") as f:
        f.write(data)
    warnings: list[str] = []
    notes: list[str] = []
    meshes_n = 0
    points = 0
    try:
        stage = Usd.Stage.Open(path)
        if stage is None:
            return {"ok": False, "warnings": ["USD 를 열 수 없습니다(손상/형식 오류)."], "notes": [], "meshes": 0}
        # 인스턴스(instanceable) 자산도 세도록 instance proxy 포함 traverse.
        meshes = [p for p in Usd.PrimRange.Stage(stage, Usd.TraverseInstanceProxies()) if p.IsA(UsdGeom.Mesh)]
        meshes_n = len(meshes)
        # 참조 분석 먼저 — 메시 0 의 원인이 '참조 누락'이면 그쪽 안내가 더 정확.
        rwarn, rnote = _analyze_refs(path, ext)
        warnings += rwarn
        notes += rnote
        if meshes_n == 0 and not rwarn:
            warnings.append("메시(형상)를 찾지 못했습니다 — 렌더할 게 없습니다.")
        dp = stage.GetDefaultPrim()
        if not (dp and dp.IsValid()):
            notes.append("defaultPrim 없음 — 카메라 궤도 방식이라 문제없이 렌더됩니다.")
        if meshes_n <= 2000:   # 대형/인스턴스면 정점 합산이 느려 생략(메시 수로만 경고)
            subdiv = sum(1 for p in meshes if UsdGeom.Mesh(p).GetSubdivisionSchemeAttr().Get() in ("catmullClark", "loop", "bilinear"))
            if subdiv:
                notes.append(f"서브디비전 표면 {subdiv}개 — 렌더 시 세분되어 다소 느릴 수 있습니다.")
            for p in meshes:
                pts = UsdGeom.Mesh(p).GetPointsAttr().Get()
                points += len(pts) if pts else 0
            if points > 3_000_000:
                notes.append(f"정점 {points:,}개(대형) — 고해상도·다바퀴면 렌더가 오래 걸립니다.")
        else:
            notes.append(f"부품(메시) {meshes_n:,}개(대형/인스턴스) — 렌더가 오래 걸릴 수 있습니다.")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "warnings": [f"검사 중 오류: {exc}"], "notes": [], "meshes": 0}
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return {"ok": True, "meshes": meshes_n, "points": points, "warnings": warnings, "notes": notes}


@router.post("/inspect")
async def inspect(file: UploadFile = File(...), _gate: None = Depends(require_auth)) -> dict[str, Any]:
    ext = PurePath(file.filename or "").suffix.lower()
    if ext not in _SUPPORTED:
        raise HTTPException(status_code=400, detail="USD 형식만 지원합니다.")
    data = await file.read()
    if len(data) > _MAX_FILE:
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다(최대 1GB).")
    return _inspect(data, ext)


@router.post("/detect-joints")
async def detect_joints(file: UploadFile = File(...), _gate: None = Depends(require_auth)) -> dict[str, Any]:
    ext = PurePath(file.filename or "").suffix.lower()
    if ext not in _SUPPORTED:
        raise HTTPException(status_code=400, detail="USD 형식만 지원합니다.")
    data = await file.read()
    if len(data) > _MAX_FILE:
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다(최대 1GB).")
    try:
        joints = _detect_joints(data, ext)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"조인트 감지 오류: {exc}") from None
    return {"joints": joints, "count": len(joints)}


@router.post("/render-submit")
async def render_submit(
    file: UploadFile = File(...),
    turns: float = Form(1.0),
    spin_dir: int = Form(1),          # +1 CCW / -1 CW
    seconds: float = Form(12.0),
    fps: int = Form(24),
    res: int = Form(1080),            # 720 / 1080 / 1440
    zoom: str = Form("false"),
    zoom_mult: float = Form(2.0),
    zoom_in: float = Form(0.46),       # 줌인 시점(영상 진행 0~1)
    zoom_out: float = Form(0.66),      # 줌아웃 시점(영상 진행 0~1)
    motors: str = Form("false"),
    motor_mode: str = Form("hold"),   # hold | cycle
    motor_deg: float = Form(90.0),
    motor_targets: str = Form(""),    # JSON {prim_path: deg} (감지 후 사용자 지정 각도)
    clean: str = Form("true"),        # 입력 자동 정리(거대 평면 제거, 재질 유지)
    mode: str = Form("cinematic"),    # cinematic(헤드리스 깨끗 렌더) | gui(Isaac GUI 화면 통째 녹화)
    quality: str = Form("standard"),  # standard(RTX 실시간) | hq(RTX+DLSS·반사·AO) | pt(PathTracing 최고화질·느림)
    _gate: None = Depends(require_auth),
) -> dict[str, Any]:
    ext = PurePath(file.filename or "").suffix.lower()
    if ext not in _SUPPORTED:
        raise HTTPException(status_code=400, detail="USD 형식만 지원합니다(.usd/.usda/.usdc/.usdz).")
    data = await file.read()
    if len(data) > _MAX_FILE:
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다(최대 1GB).")
    render_mode = "gui" if str(mode).lower() == "gui" else "cinematic"
    if render_mode == "gui":
        if not isaac.gui_turntable_available():
            raise HTTPException(status_code=400, detail="GUI 화면녹화 모드 불가 — Isaac GUI 런처/ffmpeg(ddagrab)가 필요하고, 디스플레이가 있는 데스크톱 세션에서만 됩니다.")
    elif not isaac.turntable_available():
        raise HTTPException(status_code=400, detail="Isaac Sim 이 이 머신에 설치돼 있지 않습니다.")

    fname = file.filename or "asset.usd"
    # 옵션 정규화/클램프
    z = str(zoom).lower() == "true"
    mo = str(motors).lower() == "true"
    mode = motor_mode if motor_mode in ("hold", "cycle") else "hold"
    rr = min(int(res), 1440) if int(res) in (720, 1080, 1440) else 1080
    tn = max(0.25, min(float(turns), 5.0))
    sd = 1 if int(spin_dir) >= 0 else -1
    sec = max(2.0, min(float(seconds), 60.0))
    f = max(12, min(int(fps), 60))
    zm = max(1.1, min(float(zoom_mult), 5.0))
    zi = max(0.0, min(float(zoom_in), 1.0))
    zo = max(0.0, min(float(zoom_out), 1.0))
    mdeg = max(5.0, min(float(motor_deg), 180.0))
    do_clean = str(clean).lower() == "true"
    q = quality.lower() if quality.lower() in ("standard", "hq", "pt") else "standard"
    mt = ""
    if motor_targets.strip():
        try:
            mt = _json.dumps({str(k): float(v) for k, v in _json.loads(motor_targets).items()})
        except Exception:
            mt = ""

    def _job() -> dict[str, Any]:
        fd, path = tempfile.mkstemp(suffix=ext)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        try:
            if render_mode == "gui":
                # Isaac GUI 앱 화면(UI 포함)을 ddagrab 으로 녹화 — 회전 + 옵션 줌(렌즈) + 옵션 모터(조인트 구동).
                mp4 = isaac.record_gui_turntable(
                    path, seconds=sec, fps=f, turns=tn, spin_dir=sd, clean=do_clean,
                    zoom=z, zoom_mult=zm, zoom_in=zi, zoom_out=zo,
                    motors=mo, motor_mode=mode, motor_deg=mdeg, motor_targets=mt,
                )
            else:
                _mult = 30 if q == "pt" else (10 if q == "hq" else 8)   # PT/HQ는 프레임당 update 가 많아 더 길게
                _cap = 7200 if q == "pt" else 3600                       # PT 최대 2시간, 그 외 60분
                mp4 = isaac.render_turntable_video(
                    path, turns=tn, spin_dir=sd, seconds=sec, fps=f, res=rr,
                    zoom=z, zoom_mult=zm, zoom_in=zi, zoom_out=zo,
                    motors=mo, motor_mode=mode, motor_deg=mdeg,
                    clean=do_clean, motor_targets=mt, quality=q,
                    timeout=min(_cap, int(600 + f * sec * _mult)),  # 프레임 수·화질 비례(무거운 자산 대비)
                )
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        stem = PurePath(fname).stem
        suffix = "_gui" if render_mode == "gui" else ""
        rec = storage.register_asset(
            _WF_ID, f"{stem} 턴테이블{' (GUI)' if render_mode == 'gui' else ''}",
            f"{stem}_turntable{suffix}.mp4", mp4,
            {"stage": "turntable", "mode": render_mode, "turns": tn, "zoom": z, "motors": mo, "res": rr, "fps": f, "quality": q},
        )
        return {"video": rec}

    return {"job_id": jobs.submit(_job)}
