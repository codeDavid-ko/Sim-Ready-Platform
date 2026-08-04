"""SR.002 썸네일 — 에셋의 실제 메시를 소프트웨어 래스터(z-buffer+Lambert)로 256² PNG.
RTX/MDL 불필요. 검증기는 존재만 확인하지만, 진짜 형상 렌더를 만든다(render_thumb.py 일반화)."""
from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

W = H = 256


def render(usd_path: Path) -> Path:
    """<asset_dir>/.thumbs/256x256/<usd_name>.png 생성하고 경로 반환.
    1순위: Isaac RTX 렌더(라이트는 스테이지 라이트 있으면 그걸로·없으면 디폴트 — isaac_render.py 자동).
    Isaac 불가/실패 시 소프트웨어 래스터로 폴백."""
    out = usd_path.parent / ".thumbs" / "256x256" / f"{usd_path.name}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        from ..material_usd import isaac
        if isaac.isaac_available():
            # 고해상도(1024)로 RTX 렌더 → 256 다운스케일(슈퍼샘플링=깔끔).
            # 조명은 lights="auto" — 자산에 authored 된 라이트가 있으면 그것, 없으면 디폴트(Distant+Dome).
            # 예전엔 "thumbnail" 리그(RectLight 15000+Dome 1500)를 썼는데, 밝기는 높지만 배경도 같이
            # 밝아져 **피사체 대비가 죽고 하이라이트가 날아간다**. 12종 실측(대비 − 포화×2):
            #   auto 11승 / 리그 1승. 극단 예 Paint_Bucket — 리그 대비 6.2·백색포화 62%,
            #   auto 대비 184.1·포화 0%. 사람이 보는 프리뷰(PDF Q5)이므로 대비를 우선한다.
            # dome=250: 배경색은 DomeLight 강도로 정해진다. Dome 이 없는 자산은 배경이 검고
            # 있는 자산은 회색이라 썸네일이 들쭉날쭉해진다 → 250 으로 못박아 **배경을 중간회색(156)으로
            # 통일**. 실측 4종(어두운 드라이버·랙·볼라드·청색 스탠드) 배경 156 일치, 대비 74.7~110.3,
            # 백색포화 0%. 더 올리면(500→200, 900→225) 배경이 밝아져 대비가 죽는다.
            imgs = isaac.render_usd_multiangle(str(usd_path), views=1, res=1024,
                                               timeout=600, lights="auto", dome=250.0)
            if imgs:
                import io
                from PIL import Image
                im = Image.open(io.BytesIO(imgs[0][1])).convert("RGB").resize((W, H), Image.LANCZOS)
                im.save(str(out), "PNG")
                return out
    except Exception:  # noqa: BLE001 — Isaac 실패 시 소프트웨어 래스터 폴백
        pass
    import numpy as np
    from pxr import Usd, UsdGeom

    stage = Usd.Stage.Open(str(usd_path))
    tris = []
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        m = UsdGeom.Mesh(prim)
        pts = m.GetPointsAttr().Get()
        counts = m.GetFaceVertexCountsAttr().Get()
        idx = m.GetFaceVertexIndicesAttr().Get()
        if not pts or not counts or not idx:
            continue
        P = np.array(pts, float)
        xf = np.array(UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default()), float)
        Pw = (np.c_[P, np.ones(len(P))] @ xf)[:, :3]
        counts = np.array(counts, int); idx = np.array(idx, int)
        o = 0
        for c in counts:
            f = idx[o:o + c]; o += c
            for k in range(1, c - 1):
                tris.append([Pw[f[0]], Pw[f[k]], Pw[f[k + 1]]])
    out = usd_path.parent / ".thumbs" / "256x256" / f"{usd_path.name}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    if not tris:
        _write_png(np.full((H, W, 3), 30, np.uint8), out)
        return out
    tris = np.array(tris, float)
    pts = tris.reshape(-1, 3)
    ctr = (pts.min(0) + pts.max(0)) / 2
    rad = max(np.linalg.norm(pts.max(0) - pts.min(0)) / 2, 1e-6)
    eye = ctr + np.array([1.1, -1.6, 0.9]) * rad * 1.5
    f = ctr - eye; f /= np.linalg.norm(f)
    up = np.array([0, 0, 1.0])
    s = np.cross(f, up); s /= np.linalg.norm(s)
    u = np.cross(s, f)
    R = np.array([s, u, -f])
    cam = (tris - eye) @ R.T
    fcl = 1.0 / math.tan(math.radians(40) / 2)
    zb = np.full((H, W), np.inf)
    img = np.zeros((H, W, 3), float); img[:] = np.array([0.10, 0.11, 0.13])
    light = np.array([0.4, -0.5, 0.8]); light /= np.linalg.norm(light)
    col = np.array([0.62, 0.66, 0.72])
    for tri in cam:
        z = -tri[:, 2]
        if np.any(z <= 0.01):
            continue
        px = (fcl * tri[:, 0] / z * 0.5 + 0.5) * W
        py = (1 - (fcl * tri[:, 1] / z * 0.5 + 0.5)) * H
        n = np.cross(tri[1] - tri[0], tri[2] - tri[0]); nn = np.linalg.norm(n)
        if nn == 0:
            continue
        shade = 0.25 + 0.75 * max(0.0, abs(float((n / nn) @ (R.T @ light))))
        color = np.clip(col * shade, 0, 1)
        minx = max(int(np.floor(px.min())), 0); maxx = min(int(np.ceil(px.max())), W - 1)
        miny = max(int(np.floor(py.min())), 0); maxy = min(int(np.ceil(py.max())), H - 1)
        if minx > maxx or miny > maxy:
            continue
        x0, y0 = px[0], py[0]; x1, y1 = px[1], py[1]; x2, y2 = px[2], py[2]
        den = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if abs(den) < 1e-9:
            continue
        ys, xs = np.mgrid[miny:maxy + 1, minx:maxx + 1]
        xc = xs + 0.5; yc = ys + 0.5
        a = ((y1 - y2) * (xc - x2) + (x2 - x1) * (yc - y2)) / den
        b = ((y2 - y0) * (xc - x2) + (x0 - x2) * (yc - y2)) / den
        cc = 1 - a - b
        inside = (a >= 0) & (b >= 0) & (cc >= 0)
        zint = a * z[0] + b * z[1] + cc * z[2]
        yy = ys.astype(int); xx = xs.astype(int)
        mk = inside & (zint < zb[yy, xx])
        zb[yy[mk], xx[mk]] = zint[mk]
        img[yy[mk], xx[mk]] = color
    _write_png((np.clip(img, 0, 1) * 255).astype(np.uint8), out)
    return out


def _write_png(arr, out: Path) -> None:
    raw = bytearray()
    for y in range(arr.shape[0]):
        raw.append(0); raw += arr[y].tobytes()

    def chunk(t, d):
        c = t + d
        return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c) & 0xffffffff)
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", arr.shape[1], arr.shape[0], 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))
    out.write_bytes(png)
