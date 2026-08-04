"""USD 자기완결(portable) 패키징 공통 유틸 — 모든 카드 공용.

문제: 우리가 만든 .usd/.usda 는 형상은 품어도 vMaterials MDL·텍스처·외부 레퍼런스를
'절대경로'로 참조한다 → 이 서버 PC 에선 열려도 **다른 PC(웹 다운로드 대상)에선 재질/형상이 깨진다.**
해결: 의존 자산을 전부 한 파일에 묶는 .usdz 로 패키징(UsdUtils.CreateNewUsdzPackage).

이 유틸을 다운로드 경계(api_workflows `/portable`)에서 호출하면 카드별 코드 없이 일괄 적용된다.
새 카드도 USD 만 등록하면 자동으로 자기완결 다운로드가 제공된다.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_USD_EXT = {".usd", ".usda", ".usdc"}


def _under(child: str, parent: Path) -> bool:
    try:
        Path(child).resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def has_external_deps(path: Path) -> bool:
    """이 USD 가 자기 폴더 밖 자산(MDL/텍스처/레퍼런스) 또는 미해결 의존을 갖는지.

    True 면 단독으로 옮기면 깨진다 → usdz 패키징 필요. 못 판단하면 보수적으로 True."""
    try:
        from pxr import Sdf, UsdUtils
    except Exception:  # noqa: BLE001
        return False
    try:
        layers, assets, unresolved = UsdUtils.ComputeAllDependencies(Sdf.AssetPath(str(path)))
    except Exception:  # noqa: BLE001
        return True
    base = path.parent
    if unresolved:
        return True
    for a in assets:  # MDL/텍스처 등 자산 참조
        if not _under(a, base):
            return True
    for lyr in layers:  # sublayer/reference 레이어
        ident = getattr(lyr, "identifier", "")
        if ident and os.path.isabs(ident) and not _under(ident, base):
            return True
    return False


def package_usdz(src_path: Path) -> bytes | None:
    """src USD 를 의존자산까지 묶은 자기완결 .usdz 바이트로. 실패 시 None(best-effort)."""
    try:
        from pxr import Sdf, UsdUtils
    except Exception:  # noqa: BLE001
        return None
    fd, dst = tempfile.mkstemp(suffix=".usdz")
    os.close(fd)
    try:
        ok = UsdUtils.CreateNewUsdzPackage(Sdf.AssetPath(str(src_path)), dst)
        if ok and os.path.exists(dst) and os.path.getsize(dst) > 0:
            return Path(dst).read_bytes()
        return None
    except Exception:  # noqa: BLE001
        return None
    finally:
        try:
            os.remove(dst)
        except OSError:
            pass


def portable_path(asset_path: Path) -> Path:
    """다운로드용 자기완결 파일 경로를 돌려준다(필요 시 패키징·캐시).

    - .usdz/비USD → 원본 그대로(이미 자기완결이거나 패키징 불필요).
    - .usd/.usda/.usdc → 외부 의존 있으면 같은 폴더에 <stem>.portable.usdz 로 1회 패키징·캐시.
      외부 의존 없으면(이미 자기완결) 원본 그대로.
    """
    ext = asset_path.suffix.lower()
    if ext not in _USD_EXT:
        return asset_path
    if not has_external_deps(asset_path):
        return asset_path
    cached = asset_path.with_suffix(".portable.usdz")
    if cached.exists() and cached.stat().st_size > 0:
        return cached
    data = package_usdz(asset_path)
    if data:
        try:
            cached.write_bytes(data)
            return cached
        except OSError:
            pass
    return asset_path  # 패키징 실패 → 원본(최소한 형상은 보임)
