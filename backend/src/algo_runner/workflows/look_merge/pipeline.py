"""룩 합치기 — 형상 USD + 디자이너 룩(머티리얼 레이어) → 자기완결 단일 .usdz.

흐름:
  1) 룩 파일이 형상을 sublayer 로 참조(흔함)하면 그 sublayer 이름으로 형상을 옆에 두어 합성.
     룩이 순수 머티리얼 오버레이면 [룩(강함), 형상] 순으로 sublayer 한 루트를 만든다.
  2) 합성 스테이지를 flatten → 형상+룩+바인딩이 한 레이어로.
  3) localize: 온라인/미해결 MDL·텍스처(info:mdl:sourceAsset, inputs:file 의 http(s) 경로)를
     로컬로 내려받아 ./materials, ./textures 로 재작성(NVIDIA AA.001: 앵커드 상대경로).
  4) UsdUtils.CreateNewUsdzPackage → 텍스처·MDL 까지 한 파일에 번들된 자기완결 .usdz.

바인딩 경로가 안 맞으면(룩이 /scenes/body 를 가리키는데 형상은 /Asset/Geometry_4 등) 룩이 시각적으로
안 붙으므로, 룩의 의도 바인딩 중 형상에 실제로 안착한 개수를 리포트해 사용자가 알 수 있게 한다.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import urllib.request
from pathlib import Path, PurePath
from typing import Any

_USD_EXT = {".usd", ".usda", ".usdc", ".usdz"}


def _norm_url(p: str) -> str:
    """USD 가 정규화한 'https:/...'(슬래시 1개)를 정상 URL 로 복원."""
    if p.startswith("https://") or p.startswith("http://"):
        return p
    if p.startswith("https:/"):
        return "https://" + p[len("https:/"):]
    if p.startswith("http:/"):
        return "http://" + p[len("http:/"):]
    return p


def _is_remote(p: str) -> bool:
    pl = p.lower()
    return pl.startswith(("http:/", "https:/"))


def _asset_basename(p: str) -> str:
    """경로의 파일명. usdz 내부참조 'pkg.usdz[0/X.mdl]' 형태면 브래킷을 벗겨 'X.mdl' 만 취한다."""
    p = p.replace("\\", "/")
    if "[" in p:
        p = p.split("[")[-1].rstrip("]")
    return os.path.basename(p)


def _read_asset_to(resolved: str, dst: str) -> bool:
    """resolved(실제 OS 파일 또는 usdz 내부 [..] 경로)를 dst 로 복사. usdz 내부면 Ar 리졸버로 읽는다."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        if os.path.exists(resolved):
            shutil.copy(resolved, dst)
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        from pxr import Ar
        a = Ar.GetResolver().OpenAsset(Ar.ResolvedPath(resolved))
        if a:
            buf = a.GetBuffer()
            with open(dst, "wb") as f:
                f.write(bytes(buf))
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _look_bindings(look_layer) -> dict[str, str]:
    """룩 레이어의 raw material:binding spec(over 포함) → {prim_path: material_path}."""
    out: dict[str, str] = {}

    def walk(spec):
        for child in spec.nameChildren:
            pr = look_layer.GetPrimAtPath(child.path)
            if pr:
                rel = pr.relationships.get("material:binding")
                if rel and rel.targetPathList.explicitItems:
                    out[str(child.path)] = str(list(rel.targetPathList.explicitItems)[0])
            walk(child)

    walk(look_layer.pseudoRoot)
    return out


_REMAP_SYS = (
    "You match a designer LOOK's parts to a target geometry whose prim names/paths differ. "
    "Each LOOK part has a semantic name (e.g. body, hinge_front, hinge_back, cap_front, cap_back, lid, door, frame) "
    "and an assigned material. The GEOMETRY parts have generic names but real world AABBs (center_m, size_m, in stage units).\n"
    "Map EACH look part to exactly ONE geometry part (1:1 if possible) using BOTH shape and name meaning:\n"
    "- 'body'/'frame'/'housing' → the largest / main volume part.\n"
    "- 'cap'/'lid'/'door'/'panel' → flat panel parts (one large dimension small).\n"
    "- 'hinge'/'pin'/'rod' → thin elongated parts (two small dimensions).\n"
    "- 'front'/'back'/'left'/'right'/'top'/'bottom' → disambiguate same-shaped pairs by position (center along an axis).\n"
    "Return STRICT JSON only: {\"mapping\": {\"<look_part_name>\": \"<geometry_prim_path>\"}} . "
    "Use geometry_prim_path verbatim from the GEOMETRY list. Omit a look part only if nothing plausibly matches."
)


def _geometry_parts(stage) -> list[dict[str, Any]]:
    """합성 스테이지에서 '정의된' 메시 prim 들의 월드 AABB(매칭용)."""
    from pxr import Sdf, Usd, UsdGeom

    bc = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    out: list[dict[str, Any]] = []
    for prim in stage.Traverse():
        if not (prim.IsA(UsdGeom.Mesh) and prim.GetSpecifier() == Sdf.SpecifierDef):
            continue
        try:
            rng = bc.ComputeWorldBound(prim).ComputeAlignedRange()
            lo, hi = rng.GetMin(), rng.GetMax()
        except Exception:  # noqa: BLE001
            continue
        if rng.IsEmpty():
            continue
        out.append({
            "path": str(prim.GetPath()), "name": prim.GetName(),
            "center_m": [round(float((lo[i] + hi[i]) * 0.5), 3) for i in range(3)],
            "size_m": [round(float(hi[i] - lo[i]), 3) for i in range(3)],
        })
    return out


async def _ai_remap(look_binds: dict[str, str], stage, api_key: str, oauth_token: str,
                    model: str) -> tuple[dict[str, str], list[dict]]:
    """경로가 안 맞을 때 Claude 로 룩 부품 ↔ 형상 부품 매칭. 반환 ({look_name: geo_path}, geo_parts)."""
    import json as _json

    from ..mass_physics.pipeline import _call_llm

    geo_parts = _geometry_parts(stage)
    look_parts = [{"name": os.path.basename(lp.replace("\\", "/")), "material": os.path.basename(mp.replace("\\", "/"))}
                  for lp, mp in look_binds.items()]
    if not geo_parts or not look_parts:
        return {}, geo_parts
    user = ("LOOK parts to place:\n" + _json.dumps(look_parts, ensure_ascii=False)
            + "\n\nGEOMETRY parts (world AABB):\n" + _json.dumps(geo_parts, ensure_ascii=False))
    data = await _call_llm(_REMAP_SYS, user, api_key, oauth_token, model)
    mapping = data.get("mapping") if isinstance(data, dict) else None
    valid = {p["path"] for p in geo_parts}
    out: dict[str, str] = {}
    for lname, gpath in (mapping or {}).items():
        if isinstance(gpath, str) and gpath in valid:
            out[str(lname)] = gpath
    return out, geo_parts


def _resolve_deps(flat_path: str, work: str,
                  resources: list[tuple[str, bytes]] | None,
                  fetch_remote: bool = True) -> tuple[list[str], list[str], list[str]]:
    """flat 레이어의 모든 에셋 경로(MDL·텍스처)를 자기완결로 해결한다.
      - 원격(http): 내려받아 ./materials | ./textures 로 묶고 경로 재작성.
      - 로컬 존재: 그대로(패키지에 포함됨).
      - 로컬 누락: 업로드된 추가 리소스(같은 파일명)로 채우고, 없으면 그 참조를 비운다(usdz 성공 보장).
    반환 (번들된 목록, 받기 실패 목록, 끝내 누락된 목록)."""
    from pxr import Sdf, Usd, UsdShade

    matdir = os.path.join(work, "materials")
    texdir = os.path.join(work, "textures")
    os.makedirs(matdir, exist_ok=True)
    os.makedirs(texdir, exist_ok=True)

    # 업로드된 추가 리소스를 work 루트 + basename 으로 둔다(상대참조가 바로 풀리게).
    res_by_base: dict[str, str] = {}
    for nm, by in (resources or []):
        base = os.path.basename(nm.replace("\\", "/"))
        if not base:
            continue
        dst = os.path.join(work, base)
        with open(dst, "wb") as f:
            f.write(by)
        res_by_base[base] = dst

    got: list[str] = []
    failed: list[str] = []
    missing: list[str] = []
    seen: set[str] = set()

    def fetch(url: str, dst: str) -> bool:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            urllib.request.urlretrieve(url, dst)  # noqa: S310 (공개 NVIDIA 콘텐츠)
            return True
        except Exception:  # noqa: BLE001
            return False

    def handle(pathstr: str, resolved: str, kind: str, set_rel, clear) -> None:
        if not pathstr:
            return
        if _is_remote(pathstr):
            if not fetch_remote:          # 원격을 안 받으면 참조를 비워 usdz 가 깨지지 않게 한다.
                clear()
                missing.append(pathstr)
                return
            url = _norm_url(pathstr)
            base = os.path.basename(url.split("?")[0]) or ("mod.mdl" if kind == "mdl" else "tex.bin")
            sub = "materials" if kind == "mdl" else "textures"
            dst = os.path.join(work, sub, base)
            if url not in seen:
                seen.add(url)
                if fetch(url, dst):
                    got.append(f"{sub}/{base}")
                    if kind == "mdl":   # MDL 안의 실제 텍스처 인자도 같은 폴더 기준으로 수집
                        try:
                            txt = open(dst, encoding="utf-8", errors="ignore").read()
                        except OSError:
                            txt = ""
                        for rel in set(re.findall(r'texture_2d\(\s*"([^"]+)"', txt)):
                            rc = rel.lstrip("./")
                            turl = url.rsplit("/", 1)[0] + "/" + rc
                            if turl in seen:
                                continue
                            seen.add(turl)
                            if fetch(turl, os.path.join(matdir, rc)):
                                got.append(f"materials/{rc}")
                else:
                    failed.append(url)
            set_rel(f"./{sub}/{base}")
            return
        # 로컬 참조: 리졸버가 찾는 경우.
        if resolved:
            # 이미 ./앵커드이고 work 안에 실재하면 그대로(자기완결+AA.001 OK).
            if pathstr.startswith("./") and os.path.exists(os.path.join(work, pathstr[2:].lstrip("/"))):
                return
            # 리졸브되지만 비앵커(예: '0/X.mdl' usdz 내부, 절대경로) → ./materials|textures 로 복사+앵커(AA.001).
            sub = "materials" if kind == "mdl" else "textures"
            b = _asset_basename(pathstr) or ("mod.mdl" if kind == "mdl" else "tex.bin")
            dst = os.path.join(work, sub, b)
            if _read_asset_to(resolved, dst):
                got.append(f"{sub}/{b}")
                set_rel(f"./{sub}/{b}")
            return
        base = _asset_basename(pathstr)
        if base in res_by_base:                       # 추가 업로드로 채움
            absp = os.path.normpath(os.path.join(work, base))
            shutil.copy(res_by_base[base], absp)
            got.append(base)
            set_rel(f"./{base}")
            return
        # work 어디든 같은 이름이 있으면(예: 다른 usdz에서 풀린) 그걸로 채움
        hits = list(Path(work).rglob(base)) if base else []
        if hits:
            sub = "materials" if kind == "mdl" else "textures"
            dst = os.path.join(work, sub, base)
            if _read_asset_to(str(hits[0]), dst):
                got.append(f"{sub}/{base}")
                set_rel(f"./{sub}/{base}")
                return
        clear()                                       # 끝내 없음 → 참조 비움(usdz 가 깨지지 않게)
        missing.append(base)

    stage = Usd.Stage.Open(flat_path)
    for prim in stage.Traverse():
        if not prim.IsA(UsdShade.Shader):
            continue
        sh = UsdShade.Shader(prim)
        a = prim.GetAttribute("info:mdl:sourceAsset")
        v = a.Get() if a else None
        if v and str(v.path):
            handle(str(v.path), str(v.resolvedPath or ""), "mdl",
                   lambda np, _a=a: _a.Set(Sdf.AssetPath(np)),
                   lambda _a=a: _a.Clear())   # 끝내 못 찾는 MDL(예: 사라진 임시 usdz)은 비워 패키징이 안 깨지게
        for inp in sh.GetInputs():
            val = inp.Get()
            if isinstance(val, Sdf.AssetPath) and str(val.path):
                handle(str(val.path), str(val.resolvedPath or ""), "tex",
                       lambda np, _i=inp: _i.Set(Sdf.AssetPath(np)),
                       lambda _i=inp: _i.GetAttr().Clear())
    stage.GetRootLayer().Export(flat_path)
    return sorted(set(got)), sorted(set(failed)), sorted(set(missing))


def _fix_default_prim(flat_path: str) -> None:
    """defaultPrim 이 메시를 품지 않으면, 메시(형상)를 가장 많이 품은 최상위 prim 으로 다시 지정.
    그 형상 루트 밖에 있는 머티리얼(룩의 /World/Looks 등)은 그 루트 아래로 옮겨 검증범위에 포함."""
    from pxr import Sdf, Usd, UsdGeom, UsdShade

    s = Usd.Stage.Open(flat_path)
    if s is None:
        return
    dp = s.GetDefaultPrim()

    def meshes_under(prim) -> int:
        return sum(1 for p in Usd.PrimRange(prim) if p.IsA(UsdGeom.Mesh))

    if dp and dp.IsValid() and meshes_under(dp) > 0:
        return  # 이미 형상을 품은 defaultPrim — 그대로

    best, bestn = None, 0
    for prim in s.GetPseudoRoot().GetChildren():
        n = meshes_under(prim)
        if n > bestn:
            best, bestn = prim, n
    if best is None:
        return
    geo_root = best.GetPath()
    layer = s.GetRootLayer()

    # 형상 루트 밖의 머티리얼을 geo_root/Looks/<name> 으로 복사 + 바인딩 retarget(검증범위 포함).
    retarget: dict[str, str] = {}
    for prim in list(s.Traverse()):
        if prim.IsA(UsdShade.Material) and not prim.GetPath().HasPrefix(geo_root):
            old = prim.GetPath()
            looks = geo_root.AppendChild("Looks")
            if not s.GetPrimAtPath(looks):
                Sdf.CreatePrimInLayer(layer, looks).specifier = Sdf.SpecifierDef
                s.GetPrimAtPath(looks).SetTypeName("Scope")
            newp = looks.AppendChild(old.name)
            if not s.GetPrimAtPath(newp):
                Sdf.CopySpec(layer, old, layer, newp)
            retarget[str(old)] = str(newp)
    if retarget:
        for prim in s.Traverse():
            rel = prim.GetRelationship("material:binding")
            if rel:
                tgts = [Sdf.Path(retarget.get(str(t), str(t))) for t in rel.GetTargets()]
                if tgts:
                    rel.SetTargets(tgts)

    s.SetDefaultPrim(s.GetPrimAtPath(geo_root))
    layer.Save()


def merge(geo_bytes: bytes, geo_name: str, look_bytes: bytes, look_name: str,
          localize: bool = True,
          resources: list[tuple[str, bytes]] | None = None,
          remap: bool = False, api_key: str = "", oauth_token: str = "",
          model: str = "claude-opus-4-8") -> tuple[bytes, dict[str, Any]]:
    """형상+룩 → 자기완결 .usdz bytes + 리포트.
    remap=True 면 룩 바인딩 경로가 형상과 안 맞을 때 Claude 로 부품을 매칭해 재질을 재바인딩한다."""
    from pxr import Sdf, Usd, UsdGeom, UsdShade, UsdUtils

    geo_ext = PurePath(geo_name).suffix.lower() or ".usd"
    look_ext = PurePath(look_name).suffix.lower() or ".usd"
    if geo_ext not in _USD_EXT or look_ext not in _USD_EXT:
        raise ValueError("형상·룩 모두 USD(.usd/.usda/.usdc/.usdz)여야 합니다.")

    work = tempfile.mkdtemp(prefix="lookmerge_")
    try:
        look_path = os.path.join(work, "look" + look_ext)
        with open(look_path, "wb") as f:
            f.write(look_bytes)
        ll = Sdf.Layer.FindOrOpen(look_path)
        if ll is None:
            raise ValueError("룩 파일을 열 수 없습니다.")
        subs = [s for s in list(ll.subLayerPaths)]

        if subs:
            # 룩이 형상을 sublayer 로 참조한다. 형상은 '실제 확장자'로 저장하고(예: .usd 를 .usdz 이름으로
            # 쓰면 USD 가 zip 으로 열려다 깨져 메시 0), 룩의 첫 sublayer 를 그 실제 파일로 다시 가리킨다.
            # (바인딩/오버는 prim 경로 기준이라 파일명이 바뀌어도 그대로 적용된다.)
            geo_name_local = "geo" + geo_ext
            geo_dst = os.path.join(work, geo_name_local)
            with open(geo_dst, "wb") as f:
                f.write(geo_bytes)
            ll.subLayerPaths[:] = [f"./{geo_name_local}"] + subs[1:]
            ll.Save()
            root_path = look_path
        else:
            # 순수 머티리얼 오버레이 → [룩(강함), 형상] sublayer 루트 구성
            geo_dst = os.path.join(work, "geo" + geo_ext)
            with open(geo_dst, "wb") as f:
                f.write(geo_bytes)
            root_path = os.path.join(work, "root.usda")
            rl = Sdf.Layer.CreateNew(root_path)
            rl.subLayerPaths[:] = [f"./look{look_ext}", f"./geo{geo_ext}"]
            rl.Save()

        stage = Usd.Stage.Open(root_path)
        if stage is None:
            raise ValueError("형상+룩 합성에 실패했습니다.")

        meshes = [p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)]
        if not meshes:
            raise ValueError("합성 결과에 형상(메시)이 없습니다 — 형상 파일이 룩과 맞는지 확인하세요.")

        # 룩의 의도 바인딩 중 형상에 실제 안착한 개수(경로 일치 여부).
        # 단순 IsValid 는 고아 over(형상 없는 빈 over)도 True 라, '정의(def)된' prim 만 인정한다.
        look_binds = _look_bindings(ll)

        def _really(p: str) -> bool:
            pr = stage.GetPrimAtPath(p)
            return bool(pr and pr.IsValid() and pr.GetSpecifier() == Sdf.SpecifierDef)

        resolved = [p for p in look_binds if _really(p)]

        # AI 구조 매칭 — 경로가 안 맞아(룩 바인딩이 형상 prim 에 안 붙음) 안 입혀질 때, Claude 로
        # 룩 부품 ↔ 형상 부품을 bbox·이름으로 매칭해 재질을 형상 prim 에 재바인딩한다.
        remap_used: dict[str, str] = {}
        if remap and look_binds and len(resolved) < len(look_binds) and (api_key or oauth_token):
            import asyncio
            try:
                mapping, _gp = asyncio.run(_ai_remap(look_binds, stage, api_key, oauth_token, model))
            except Exception:  # noqa: BLE001
                mapping = {}
            for lp, mp in look_binds.items():
                lname = os.path.basename(lp.replace("\\", "/"))
                gpath = mapping.get(lname)
                if not gpath:
                    continue
                gprim = stage.GetPrimAtPath(gpath)
                matprim = stage.GetPrimAtPath(mp)
                if gprim and gprim.IsValid() and matprim and matprim.IsValid():
                    UsdShade.MaterialBindingAPI.Apply(gprim).Bind(UsdShade.Material(matprim))
                    remap_used[os.path.basename(lp.replace("\\", "/"))] = os.path.basename(gpath)

        look_mats = sorted({str(m.GetPath()) for m in stage.Traverse() if m.IsA(UsdShade.Material)})

        # flatten → 단일 레이어. 내부 USD 이름 = 에셋(형상 파일) 이름 (예전엔 항상 "merged.usd" 였음).
        # NVIDIA 는 root-usd 이름을 강제하지 않으므로 에셋명이 패키지 안 root 가 된다(검증/패키징 무관).
        flat = stage.Flatten()
        _asset_nm = "".join(c if (c.isalnum() or c in "_-") else "_" for c in PurePath(geo_name).stem).strip("_") or "asset"
        flat_path = os.path.join(work, _asset_nm + ".usd")
        flat.Export(flat_path)

        # defaultPrim 보정 — 룩 레이어의 defaultPrim(예: /World)이 형상(/Asset)과 다르면, NVIDIA 검증이
        # defaultPrim 아래만 보므로 강체·메시·충돌이 "없음"으로 판정된다. → 형상이 든 루트로 다시 지정.
        _fix_default_prim(flat_path)

        # 의존(텍스처·MDL) 해결 — usdz 로 묶으려면 모든 참조가 로컬이어야 하므로 항상 수행.
        # fetch_remote=localize: 켜짐이면 온라인 받아 묶고, 꺼짐이면 온라인 참조를 비운다.
        localized, failed, missing = _resolve_deps(flat_path, work, resources, fetch_remote=localize)

        out_z = os.path.join(work, PurePath(geo_name).stem + "_shaded.usdz")
        UsdUtils.CreateNewUsdzPackage(Sdf.AssetPath(flat_path), out_z)
        with open(out_z, "rb") as f:
            data = f.read()

        # 최종 자기완결 검증
        _, _, unresolved = UsdUtils.ComputeAllDependencies(Sdf.AssetPath(out_z))
        unres = sorted({str(u) for u in (unresolved or [])})

        report = {
            "meshes": len(meshes),
            "materials": look_mats,
            "look_bindings_total": len(look_binds),
            "look_bindings_resolved": len(resolved),
            "binding_paths_unmatched": sorted(set(look_binds) - set(resolved))[:12],
            "remapped": len(remap_used),                # AI 구조 매칭으로 재바인딩된 수
            "remap_mapping": remap_used,                # {룩부품: 형상부품}
            "localized": localized,
            "localize_failed": failed,
            "missing_textures": missing,
            "self_contained": len(unres) == 0,
            "unresolved": unres[:12],
            "usdz_bytes": len(data),
        }
        return data, report
    finally:
        shutil.rmtree(work, ignore_errors=True)
