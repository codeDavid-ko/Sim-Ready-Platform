"""mass_physics 파이프라인 — ndotsim 물성 추론을 Sim-Ready 플랫폼 카드로 구현.

흐름(Notion 사양):
  STEP/STL → 형상 정확 질량특성(trimesh, LLM 아님) → Claude Stage1(재질 분류)
  → Claude Stage2(접촉 물리값 + clamp) → mass=ρ×V · UsdPhysics 단일 .usd → usdchecker.

HARD RULE: mass·volume·inertia·CoM 은 형상에서 결정론적으로 계산한다. LLM 은 재질
(→density)과 접촉계수(friction/restitution)만 정한다.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from typing import Any

import numpy as np

from ..material_usd import common
from .reference import MATERIALS, TAXONOMY

_UNIT_TO_M = {"m": 1.0, "mm": 0.001, "cm": 0.01, "in": 0.0254}


# ───────────────────────── 1. 형상 정확 질량특성 ─────────────────────────
def apply_shell_correction(parts: list[dict[str, Any]], thickness_mm: float, scope: str = "auto") -> list[dict[str, Any]]:
    """속 빈 형상(프레임/외함/판금) 보정 — 부피를 '표면적 × 벽두께(쉘)'로 대체해 질량 과대를 잡는다.

    비폐쇄 메시는 부피가 convex_hull/bbox(꽉 찬 solid)로 과대평가됨 → 실제는 얇은 벽.
    scope="auto": mesh_exact(폐쇄 솔리드, 정확)는 건드리지 않고, 과대평가된 부품만 보정.
    scope="all": 모든 부품에 적용. 쉘부피가 기존부피보다 작을 때만 적용(질량 늘리지 않음).
    질량은 이후 infer 의 mass = density × volume_m3 로 재계산된다."""
    t = max(0.0, float(thickness_mm)) / 1000.0  # m
    if t <= 0:
        return parts
    for p in parts:
        if scope == "auto" and p.get("volume_method") == "mesh_exact":
            continue
        area = float(p.get("area_m2", 0.0)); vol = float(p.get("volume_m3", 0.0))
        vshell = area * t
        if vshell > 0 and vshell < vol:
            sc = vshell / vol
            p["volume_m3"] = round(vshell, 9)
            p["inertia_geom_m5"] = (np.asarray(p["inertia_geom_m5"], float) * sc).tolist()
            p["volume_method"] = f"shell({thickness_mm:g}mm)"
    return parts


def _part_geometry(name: str, V_m: np.ndarray, F: np.ndarray) -> dict[str, Any]:
    """미터 좌표 메시 → 정확 부피/면적/관성/CoM + Stage1 features.

    watertight 면 mesh.volume(정확). 아니면 convex_hull 폴백(중공 과대 가능).
    trimesh moment_inertia 는 density=1 기준(= I_geom, 단위 m^5)."""
    import trimesh

    mesh = trimesh.Trimesh(vertices=V_m, faces=F, process=False)
    mn, mx = V_m.min(0), V_m.max(0)
    dims_m = (mx - mn)
    method = "mesh_exact"
    src = mesh
    if not (mesh.is_volume and mesh.volume > 1e-12):
        try:
            src = mesh.convex_hull
            method = "convex_hull"
        except Exception:  # noqa: BLE001
            src = None
            method = "bbox"
    if src is not None and getattr(src, "volume", 0) > 1e-12:
        volume = float(src.volume)
        com = [float(x) for x in src.center_mass]
        i_geom = np.asarray(src.moment_inertia, float)  # m^5 (density=1)
    else:  # bbox 폴백
        volume = float(dims_m[0] * dims_m[1] * dims_m[2])
        com = [float(x) for x in (mn + mx) / 2]
        lx, ly, lz = dims_m
        # 균일 직육면체 관성(density=1, mass=volume): (V/12)*(b^2+c^2) ...
        i_geom = np.diag([
            volume / 12.0 * (ly * ly + lz * lz),
            volume / 12.0 * (lx * lx + lz * lz),
            volume / 12.0 * (lx * lx + ly * ly),
        ])
    area = float(mesh.area) if mesh.area else 0.0
    dims_mm = [round(float(x) * 1000.0, 2) for x in dims_m]
    edges = sorted(d for d in dims_m if d > 0) or [0.0]
    thinness = round(float(edges[0] / edges[-1]), 4) if edges[-1] > 0 else 0.0
    apv = round(float(area / volume), 2) if volume > 0 else 0.0
    return {
        "name": name,
        "dims_mm": dims_mm,
        "dims_m": [float(x) for x in dims_m],
        "volume_m3": round(volume, 9),
        "area_m2": round(area, 6),
        "thinness": thinness,
        "area_per_volume_1pm": apv,
        "volume_method": method,
        "com_m": com,
        "inertia_geom_m5": i_geom.tolist(),
        "_V": V_m,
        "_F": F,
    }


def parse_geometry(file_bytes: bytes, file_name: str, in_units: str = "mm",
                   unit_override: str | None = None) -> list[dict[str, Any]]:
    """파일 → 파트별 정확 질량특성 리스트(미터 기준).

    unit_override(m/mm/cm)가 주어지면 파일이 자기서술한 단위를 무시하고 그 단위로 강제 해석한다
    (USD 의 metersPerUnit 오기입으로 질량이 비현실적일 때 사용자가 수동 보정). 없으면:
    USD 는 metersPerUnit 신뢰(scale=1.0), mesh 는 in_units 적용."""
    ext = os.path.splitext(file_name or "")[1].lower() or ".stl"
    is_usd = ext in (".usd", ".usda", ".usdc", ".usdz")
    if unit_override in _UNIT_TO_M:
        scale = _UNIT_TO_M[unit_override]   # 강제: 파일 단위 무시(USD 는 mpu≈1 가정에서 보정)
    else:
        scale = 1.0 if is_usd else _UNIT_TO_M.get(in_units, 0.001)
    fd, path = tempfile.mkstemp(suffix=ext)
    with os.fdopen(fd, "wb") as f:
        f.write(file_bytes)
    try:
        raw = common.load_parts(path)  # [(name, V(원본단위), F)]
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    if not raw:
        raise ValueError("형상에서 메시를 찾지 못했습니다.")
    parts = []
    seen: dict[str, int] = {}
    for name, V, F in raw:
        seen[name] = seen.get(name, 0) + 1
        nm = name if seen[name] == 1 else f"{name}_{seen[name]}"
        V_m = np.asarray(V, float) * scale
        parts.append(_part_geometry(nm, V_m, np.asarray(F, int)))
    return parts


# 재질 이름(MDL/시각재질 prim 이름 등) → density taxonomy 키 후보(부분일치). 긴 키부터 검사.
_MATERIAL_ALIASES: list[tuple[str, str]] = [
    # 금속 — 긴 키가 먼저(부분일치라 "stainless_steel" 이 "steel" 보다 앞에 와야 한다)
    ("stainless", "stainless_steel"), ("inox", "stainless_steel"), ("sus", "stainless_steel"),
    ("cast_iron", "cast_iron"), ("cast iron", "cast_iron"),
    ("steel", "steel"), ("iron", "steel"), ("carbon_steel", "steel"),
    # "alumina"(세라믹)는 "alu"(알루미늄)보다 **먼저** 걸려야 한다 — 부분일치라 순서가 곧 우선순위.
    ("alumina", "ceramic"), ("al2o3", "ceramic"),
    ("aluminium", "aluminum"), ("aluminum", "aluminum"), ("alu", "aluminum"),
    ("brass", "brass"), ("bronze", "brass"), ("copper", "copper"),
    # 아연: 다이캐스팅(Zamak)이 먼저 걸려야 한다 — 부분일치라 긴 키를 앞에 둔다.
    # (아연도강판 galvanized 는 소재가 강이므로 위쪽 steel 이 먼저 잡는 게 맞다 — 여기 넣지 않는다)
    ("zamak", "zinc_diecast"), ("zinc_diecast", "zinc_diecast"), ("diecast", "zinc_diecast"),
    ("die_cast", "zinc_diecast"), ("zinc", "zinc"),
    ("titan", "titanium"), ("magnesium", "magnesium"), ("lead", "lead"),
    # 플라스틱
    ("polypropylene", "polypropylene"), ("hdpe", "hdpe"), ("polyethylene", "hdpe"),
    ("polycarbonate", "polycarbonate"), ("lexan", "polycarbonate"),
    ("gf_nylon", "gf_nylon"), ("nylon", "nylon"), ("polyamide", "nylon"),
    ("acetal", "pom"), ("delrin", "pom"), ("pom", "pom"),
    ("ptfe", "ptfe"), ("teflon", "ptfe"), ("peek", "peek"),
    ("pvc", "pvc"), ("vinyl", "pvc"), ("pet", "pet"),
    ("abs", "abs"),
    # 엘라스토머·폼
    ("silicone", "silicone"), ("epdm", "epdm"), ("rubber", "rubber"),
    ("foam", "foam"), ("cushion", "foam"), ("cork", "cork"),
    # 비금속 구조재 — 목재·판재·직물류
    ("plywood", "wood"), ("timber", "wood"), ("wood", "wood"), ("oak", "wood"),
    ("pine", "wood"), ("birch", "wood"), ("mdf", "mdf"), ("chipboard", "mdf"),
    ("cardboard", "cardboard"), ("carton", "cardboard"), ("paper", "cardboard"),
    # porcelain(자기 2400)을 ceramic(알루미나 3800)으로 보내면 58% 과대 → 별도 키로.
    ("porcelain", "porcelain"), ("china", "porcelain"), ("stoneware", "porcelain"),
    ("earthenware", "porcelain"), ("alumina", "ceramic"), ("ceramic", "ceramic"),
    ("fiberglass", "fiberglass"), ("glass_fiber", "fiberglass"),
    ("carbon_fiber", "carbon_fiber"), ("carbon fiber", "carbon_fiber"),
    ("glass", "glass"),
    # 직물류는 taxonomy 에 전용 키가 없다(스펙 33종에도 없음) → 메시/천은 나일론,
    # 쿠션·시트는 폼으로 근사한다. 정확히 하려면 사람이 재질 교정 UI 에서 지정.
    ("mesh", "nylon"), ("fabric", "nylon"), ("textile", "nylon"), ("upholstery", "foam"),
    ("plastic", "abs"),
]


def material_from_hint(name: str | None) -> str | None:
    """바인딩된 시각재질 이름에서 density taxonomy 키를 추론(부분일치). 못 찾으면 None."""
    if not name:
        return None
    s = name.lower()
    for needle, key in _MATERIAL_ALIASES:
        if needle in s and key in MATERIALS:
            return key
    return None


def read_bound_materials(usd_bytes: bytes, file_name: str) -> list[str | None]:
    """입력 USD 의 메시별(바인딩된) 시각재질 이름을 stage traversal 순서로 돌려준다.
    이 순서는 parse_geometry(=load_parts) 의 메시 순서와 같아 parts 와 1:1 정렬된다.
    물성 추론이 외형 재질을 밀도 prior 로 쓰게 하기 위함. 바인딩 없으면 그 자리 None."""
    ext = os.path.splitext(file_name or "")[1].lower()
    if ext not in (".usd", ".usda", ".usdc", ".usdz"):
        return []
    try:
        from pxr import Usd, UsdGeom, UsdShade
    except Exception:  # noqa: BLE001
        return []
    fd, path = tempfile.mkstemp(suffix=ext)
    os.close(fd)
    with open(path, "wb") as f:
        f.write(usd_bytes)
    try:
        stage = Usd.Stage.Open(path)
        if stage is None:
            return []
        out: list[str | None] = []
        for prim in stage.Traverse():
            if not prim.IsA(UsdGeom.Mesh):
                continue
            name: str | None = None
            try:
                # purpose 미지정(=시각) 바인딩 우선
                bound, _rel = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
                if bound and bound.GetPrim().IsValid():
                    name = bound.GetPrim().GetName()
            except Exception:  # noqa: BLE001
                name = None
            out.append(name)
        return out
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


# ───────────────────────── 2. Claude 추론 (Stage1 / Stage2) ─────────────────────────
_STAGE1_SYS = (
    "You are a mechanical-engineering material classifier. Given CAD parts (name + "
    "geometry only), assign each part the single most likely material from this FIXED "
    "taxonomy — never invent a material outside it:\n"
    + "\n".join(f"  - {k}: density {v['density']} kg/m^3" for k, v in MATERIALS.items())
    + "\n\nAn 'Assembly / product' line and optional 'Context' may precede the parts. When "
    "present, treat the product TYPE as a STRONG prior — e.g. a foldable crate/box or tote "
    "is almost always molded plastic (polypropylene/hdpe/abs), not metal — and let it "
    "override a generic name-only guess.\n"
    "Per part you also get shape cues: `thinness` (min/max bbox edge; near 0 = a thin "
    "panel/sheet/shell, not a solid billet), `area_per_volume_1pm` (large = thin-walled or "
    "hollow), and `volume_method` (`mesh_exact` = trustworthy volume; `convex_hull`/`bbox` = "
    "volume may be over-estimated for a hollow part). A thin large panel is consistent with "
    "BOTH a plastic enclosure wall and sheet metal — disambiguate using the product context.\n"
    "If RENDERED IMAGES of the asset are attached, LOOK AT THEM FIRST: identify what the "
    "product is and what each part looks like (colour, finish, texture) and let that drive the "
    "choice. Images are the strongest evidence; a bare grey render still shows the SHAPE, "
    "which is useful even when the asset has no materials yet.\n"
    "Reason from the part name, the product context, and size/shape. Always pick the closest "
    "taxonomy key even when unsure; express uncertainty via `confidence` (0-1), not by "
    "refusing. Keep `reasoning` to one short sentence. Return exactly one choice per part, "
    "echoing its part_index.\n"
    "If a part includes `bound_visual_material` (the visual/appearance material already assigned "
    "upstream, e.g. 'alu_frame', 'steel_painted_gray'), treat it as the STRONGEST signal for the "
    "physical material/density — map it to the matching taxonomy key (alu*→aluminum, steel*→steel, "
    "etc.) unless it is clearly impossible, and reflect that in `reasoning`.\n\n"
    'Respond with ONLY a JSON object, no prose: {"choices":[{"part_index":int,'
    '"material":"<taxonomy key>","confidence":0..1,"reasoning":"one sentence"}]}'
)

_STAGE2_SYS = (
    "You are a contact-physics estimator for rigid-body simulation. For each CAD part you "
    "are given its material, geometry, and the ALLOWED RANGE [lo, hi] for each contact "
    "parameter (static_friction, dynamic_friction, restitution; dry-contact, PhysX "
    "RigidBodyMaterial convention).\n"
    "Pick one value per parameter, reasoning from the material and likely surface finish/use. "
    "You MUST stay inside each parameter's allowed range, and keep dynamic_friction <= "
    "static_friction. Keep `reasoning` to one short sentence. Return exactly one choice per "
    "part, echoing its part_index.\n\n"
    'Respond with ONLY a JSON object, no prose: {"choices":[{"part_index":int,'
    '"static_friction":float,"dynamic_friction":float,"restitution":float,'
    '"reasoning":"one sentence"}]}'
)


_STAGE3_SYS = (
    "You are a mechanical-engineering mass sanity checker. Some CAD parts come from "
    "NON-watertight meshes, so their volume was estimated as a SOLID block (convex_hull/bbox) "
    "and their mass is likely far too heavy — real frames, racks, enclosures, brackets, ducts "
    "and sheet-metal panels are HOLLOW (thin walls), not solid billets.\n"
    "For each candidate part you get: name, material, density (kg/m^3), bbox dims (mm), the "
    "SOLID-estimate mass (kg) and the surface area (m^2). Decide whether the part is "
    "realistically thin-walled/hollow. If so, give a wall thickness in mm so that the resulting "
    "shell mass = density * area * (wall_mm/1000) is a BELIEVABLE real-world weight for that "
    "kind of part — typical sheet metal 1.5-3mm, structural tube/frame 2-6mm, plastic enclosure "
    "2-4mm. If the part is genuinely a solid billet/casting (the solid mass is already "
    "plausible), return hollow=false and wall_mm=0. Sanity-check the WHOLE assembly's total mass "
    "against what such a product really weighs. Keep reasoning to one short sentence, echo "
    "part_index.\n\n"
    "\n\n"
    "You may also be shown RENDERED IMAGES of the actual asset and a product Context line. "
    "Use them to identify WHAT THE PRODUCT IS, then establish what such a product REALLY WEIGHS "
    "in total.\n"
    "IF A WebSearch TOOL IS AVAILABLE, USE IT — do not rely on memory for the weight. Once you "
    "can name the product from the render and the part names, search for its real specification "
    "(e.g. \"120L plastic wheelie bin weight kg\", \"16 oz claw hammer weight\", "
    "\"steel pallet stand 1200x1200 weight kg\", \"LED signal tower 3 stack weight\"). Prefer "
    "manufacturer spec sheets, distributor product pages and standards over forums. Take the "
    "product's own dimensions into account: only trust a figure whose size matches the dims you "
    "were given, and scale sensibly when the closest match is a different size.\n"
    "WEIGH THE EMPTY, UNLOADED PRODUCT — the asset itself, never its contents or payload. "
    "A paint pail means the EMPTY pail (~1.3 kg), NOT a full one (~26 kg); a bin means the empty "
    "bin, not the rubbish; a pallet means the bare pallet, not a pallet load; a rack means the "
    "steel structure, not the goods stored on it; a tank/bottle/crate likewise. For containers "
    "the two differ by 10-20x, so state 'empty' in `product_identified` and search for the empty "
    "weight (words like \"empty\", \"tare\", \"unit weight\").\n"
    "Report the weight you settled on as `product_typical_mass_kg` (one number for the WHOLE "
    "assembly), what you decided the product is as `product_identified`, and where the figure "
    "came from as `mass_sources` (1-3 short entries: what was searched, the figure found, the "
    "site/manufacturer). If you could not search, say so in `mass_sources` and fall back to "
    "engineering judgement.\n"
    "Then choose each wall_mm so the assembly total lands near that weight — exactly as an "
    "engineer nudges a thickness slider until the weight matches the spec sheet.\n\n"
    "CHECK BOTH DIRECTIONS. You are given EVERY part, including watertight solids, precisely "
    "because assemblies also come out TOO LIGHT — and a wall thickness can never fix that.\n"
    "  * TOO HEAVY: the part is really thin-walled but its volume was measured as a solid block "
    "-> hollow=true with a wall_mm. Parts flagged `shell_candidate` (non-watertight mesh) are the "
    "usual case. A WATERTIGHT part may also be declared hollow, but ONLY when the real product is "
    "manifestly a shell that the CAD merely modelled as a filled block — a plastic bollard, bin, "
    "bucket, bottle, cone or moulded enclosure. NEVER do this to a part that is genuinely solid: "
    "hammer heads, bolts, pins, bars, blades, castings and machined blocks keep their solid mass.\n"
    "  * TOO LIGHT: almost always a WRONG MATERIAL — a dense part labelled as a light one. "
    "Classic cases: a tool shaft that has a separate rubber grip is steel or fibreglass, NOT wood; "
    "a machine base called `abs` is really cast_iron; a road bollard is rubber or concrete-filled "
    "rather than hollow plastic. Return `material` = the correct taxonomy key and leave "
    "hollow=false. Mass is always recomputed as density x volume, so correcting the material is "
    "the ONLY legitimate way to make a part heavier — never invent a mass.\n"
    "  * Leave `material` null when the current material is right. Do NOT swap materials merely "
    "to hit a number: the render and the part's function must actually support the change.\n\n"
    'Respond with ONLY JSON: {"product_typical_mass_kg":float,"product_identified":"what it is",'
    '"mass_sources":["what was searched -> figure found (site)"],'
    '"choices":[{"part_index":int,"hollow":bool,"wall_mm":float,'
    '"material":"<taxonomy key or null>","reasoning":"one sentence"}]}'
)


def _stage3_user(parts: list[dict], idxs: list[int], context: str = "") -> str:
    """idxs = 쉘 보정 후보(비폐쇄 메시). 하지만 **모든 부품**을 실어 보낸다 —
    너무 가벼운 조립체는 벽두께로 고칠 수 없고 재질 교정만이 방법이므로 폐합 솔리드도 봐야 한다."""
    cand = set(idxs)
    feats = [{
        "part_index": i,
        "name": p["name"],
        "material": p.get("material"),
        "density": p.get("density"),
        "dims_mm": p["dims_mm"],
        "mass_kg": p.get("mass_kg"),
        "area_m2": p["area_m2"],
        "volume_method": p["volume_method"],
        "shell_candidate": i in cand,
    } for i, p in enumerate(parts)]
    total = round(sum(float(p.get("mass_kg") or 0) for p in parts), 3)
    head = (f"Context: {context}\n" if context else "")
    head += (f"Assembly total mass as currently computed: {total} kg (parts={len(parts)}, "
             f"shell candidates={len(cand)})\n"
             "Compare this against what the real product weighs: if far too light fix the wrong "
             "material(s); if far too heavy thin the hollow parts.\n")
    return head + "Parts:\n" + json.dumps(feats, ensure_ascii=False, indent=2)


def _shell_eligible(p: dict) -> bool:
    """비폐쇄 메시(부피 과대평가 가능) — mesh_exact(정확 솔리드)만 제외."""
    return str(p.get("volume_method", "")).split("(")[0] not in ("mesh_exact",)


def _snapshot_solid(p: dict) -> None:
    """현재(솔리드) 질량특성을 기준값으로 보존 — 이후 쉘 보정/다운로드 재작성의 베이스라인."""
    p["solid_volume_m3"] = p["volume_m3"]
    p["solid_inertia_geom_m5"] = p["inertia_geom_m5"]
    p["solid_mass_kg"] = p.get("mass_kg")


def apply_shell_to_part(p: dict, thickness_mm: float) -> None:
    """솔리드 기준값에서 두께 t 의 쉘로 부피·관성·질량을 재계산(상한=솔리드). 0 이면 솔리드 복원."""
    sv = float(p.get("solid_volume_m3", p["volume_m3"]))
    si = np.asarray(p.get("solid_inertia_geom_m5", p["inertia_geom_m5"]), float)
    sm = float(p.get("solid_mass_kg", p.get("mass_kg") or 0.0))
    t = max(0.0, float(thickness_mm)) / 1000.0
    vshell = float(p.get("area_m2", 0.0)) * t
    if t > 0 and 0 < vshell < sv:
        sc = vshell / sv
        p["volume_m3"] = round(vshell, 9)
        p["inertia_geom_m5"] = (si * sc).tolist()
        p["mass_kg"] = round(sm * sc, 6)
        p["volume_method"] = f"shell({thickness_mm:g}mm)"
    else:  # 솔리드 복원
        p["volume_m3"] = sv
        p["inertia_geom_m5"] = si.tolist()
        p["mass_kg"] = round(sm, 6)


def _extract_json(text: str) -> dict[str, Any]:
    """LLM 응답에서 JSON 오브젝트를 뽑는다.

    첫 '{' ~ 마지막 '}' 를 그대로 파싱하면, 응답에 JSON 이 두 개 이상 있거나(웹 검색 도구를 쓰면
    설명 + 결과처럼 나뉘어 오는 일이 있다) 뒤에 산문이 붙으면 'Extra data' 로 죽는다(실측).
    → 전체 슬라이스를 먼저 시도하고, 실패하면 raw_decode 로 **모든 후보를 훑어 가장 알맞은
    오브젝트**(choices/parts 같은 실제 payload 를 가진 것, 없으면 가장 큰 것)를 고른다."""
    s = text.find("{")
    e = text.rfind("}")
    if s < 0 or e < 0:
        raise ValueError(f"LLM 응답에서 JSON 을 찾지 못함: {text[:200]}")
    try:
        return json.loads(text[s : e + 1])
    except ValueError:
        pass
    dec = json.JSONDecoder()
    cands: list[dict[str, Any]] = []
    i = s
    while 0 <= i < len(text):
        try:
            obj, end = dec.raw_decode(text, i)
        except ValueError:
            i = text.find("{", i + 1)
            continue
        if isinstance(obj, dict):
            cands.append(obj)
        i = text.find("{", max(end, i + 1))
    if not cands:
        raise ValueError(f"LLM 응답 JSON 파싱 실패: {text[:200]}")
    keyed = [c for c in cands if any(k in c for k in ("choices", "parts", "assignments"))]
    return max(keyed or cands, key=lambda c: len(json.dumps(c)))


async def _call_llm(
    system: str, user: str, api_key: str, oauth_token: str, model: str,
    images: list[tuple[bytes, str]] | None = None, web_search: bool = False,
) -> dict[str, Any]:
    """구독(claude-agent-sdk) 우선, API 키 있으면 raw anthropic. forced JSON 파싱.
    images(참조 이미지)가 있으면 분류 프롬프트에 같이 넣는다(Stage1 재질 추론 정확도↑).

    web_search=True 면 웹 검색을 허용한다 — 질량 검산(Stage3)에서 '이 제품이 실제로 몇 kg 인지'를
    기억에만 의존하지 않고 제조사 스펙·판매 페이지로 확인시키기 위함."""
    imgs = images or []
    prompt = system + "\n\n" + user
    if api_key:
        from ..material_usd.pipeline import _classify_via_anthropic
        raw = _classify_via_anthropic(prompt, imgs, api_key, web_search=web_search)
    else:
        from ..material_usd.pipeline import _classify_via_agent
        raw = await _classify_via_agent(prompt, imgs, model,
                                        extra_tools=["WebSearch", "WebFetch"] if web_search else None)
    return _extract_json(raw)


def _stage1_user(parts: list[dict], context: str) -> str:
    feats = []
    for i, p in enumerate(parts):
        f = {
            "part_index": i,
            "name": p["name"],
            "dims_mm": p["dims_mm"],
            "volume_m3": p["volume_m3"],
            "area_m2": p["area_m2"],
            "thinness": p["thinness"],
            "area_per_volume_1pm": p["area_per_volume_1pm"],
            "volume_method": p["volume_method"],
        }
        if p.get("bound_visual_material"):
            f["bound_visual_material"] = p["bound_visual_material"]
        feats.append(f)
    head = (f"Context: {context}\n\n" if context else "") + "Parts:\n"
    return head + json.dumps(feats, ensure_ascii=False, indent=2)


def _stage2_user(parts: list[dict], materials: list[str]) -> str:
    feats = []
    for i, p in enumerate(parts):
        m = materials[i]
        ref = MATERIALS[m]
        feats.append({
            "part_index": i,
            "name": p["name"],
            "material": m,
            "dims_mm": p["dims_mm"],
            "allowed_ranges": {
                "static_friction": ref["static_friction"],
                "dynamic_friction": ref["dynamic_friction"],
                "restitution": ref["restitution"],
            },
        })
    return "Parts:\n" + json.dumps(feats, ensure_ascii=False, indent=2)


def _clamp(v: float, lo: float, hi: float) -> tuple[float, bool]:
    if v < lo:
        return lo, True
    if v > hi:
        return hi, True
    return v, False


async def infer(
    parts: list[dict[str, Any]],
    context: str = "",
    images: list[tuple[bytes, str]] | None = None,
    api_key: str = "",
    oauth_token: str = "",
    model: str = "claude-sonnet-4-6",
    auto_shell: bool = True,
    material_hints: list[str | None] | None = None,
) -> dict[str, Any]:
    """Stage1(재질)+Stage2(접촉물리)+Stage3(중공 벽두께) 추론 → parts 에 material/density/friction/
    restitution 주입 + 비폐쇄(과대질량) 부품은 LLM 이 총질량을 보고 현실적 벽두께를 정해 자동 보정.
    images(참조 이미지)는 Stage1 재질 분류에만 사용. LLM 없으면 steel 기본 + range 중앙값 폴백."""
    n = len(parts)
    clamped: list[dict] = []
    # 입력 USD 에 바인딩된 시각재질이 있으면 부품에 실어 Stage1 의 강한 prior 로 사용.
    hints = material_hints or []
    for i, p in enumerate(parts):
        h = hints[i] if i < len(hints) else None
        if h:
            p["bound_visual_material"] = h
    if not (api_key or oauth_token):
        # LLM 없으면: 바인딩 재질명을 taxonomy 로 매핑(되면), 안 되면 steel.
        mats = []
        for p in parts:
            mapped = material_from_hint(p.get("bound_visual_material"))
            p["material"] = mapped or "steel"
            p["confidence"] = 0.0
            p["material_reasoning"] = (
                f"LLM 미사용: 바인딩 재질 '{p['bound_visual_material']}' → {mapped}" if mapped
                else "LLM 미사용(인증 없음): steel 기본값"
            )
            mats.append(p["material"])
    else:
        s1 = await _call_llm(_STAGE1_SYS, _stage1_user(parts, context), api_key, oauth_token, model, images=images)
        choice_by_idx = {int(c["part_index"]): c for c in s1.get("choices", [])}
        mats = []
        for i, p in enumerate(parts):
            c = choice_by_idx.get(i, {})
            mat = c.get("material")
            if mat not in MATERIALS:
                mat = material_from_hint(p.get("bound_visual_material")) or "steel"
            p["material"] = mat
            p["confidence"] = float(c.get("confidence", 0.0))
            p["material_reasoning"] = c.get("reasoning", "")
            mats.append(mat)

    # density 는 reference 스칼라(추론 아님)
    for p, m in zip(parts, mats):
        p["density"] = MATERIALS[m]["density"]

    # Stage2 접촉물리
    if not (api_key or oauth_token):
        for p, m in zip(parts, mats):
            ref = MATERIALS[m]
            p["static_friction"] = round(sum(ref["static_friction"]) / 2, 3)
            p["dynamic_friction"] = round(sum(ref["dynamic_friction"]) / 2, 3)
            p["restitution"] = round(sum(ref["restitution"]) / 2, 3)
            p["physics_reasoning"] = "LLM 미사용: range 중앙값"
    else:
        s2 = await _call_llm(_STAGE2_SYS, _stage2_user(parts, mats), api_key, oauth_token, model)
        by_idx = {int(c["part_index"]): c for c in s2.get("choices", [])}
        for i, (p, m) in enumerate(zip(parts, mats)):
            ref = MATERIALS[m]
            c = by_idx.get(i, {})
            sf, c1 = _clamp(float(c.get("static_friction", sum(ref["static_friction"]) / 2)), *ref["static_friction"])
            df, c2 = _clamp(float(c.get("dynamic_friction", sum(ref["dynamic_friction"]) / 2)), *ref["dynamic_friction"])
            rest, c3 = _clamp(float(c.get("restitution", sum(ref["restitution"]) / 2)), *ref["restitution"])
            if df > sf:  # dynamic <= static 강제
                df = sf
                c2 = True
            p["static_friction"] = round(sf, 3)
            p["dynamic_friction"] = round(df, 3)
            p["restitution"] = round(rest, 3)
            p["physics_reasoning"] = c.get("reasoning", "")
            for key, was in (("static_friction", c1), ("dynamic_friction", c2), ("restitution", c3)):
                if was:
                    clamped.append({"part_index": i, "name": p["name"], "param": key})

    # mass = density × volume (HARD RULE) — 솔리드 기준
    for p in parts:
        p["mass_kg"] = round(p["density"] * p["volume_m3"], 6)
        _snapshot_solid(p)
        p["auto_wall_mm"] = 0.0
        p["shell_reason"] = ""

    # Stage3: 질량 양방향 검산 + 자동 보정. **쉘 후보가 없어도 항상 실행한다** —
    # 조립체가 너무 '가벼운' 경우(재질 오판: 스틸 샤프트를 wood 로 본 망치)는 벽두께로 고칠 수
    # 없고, 예전처럼 `if cand:` 게이트를 걸면 검산 자체가 일어나지 않는다(실측: 12종 중 8종이
    # 검산 없이 통과해 망치 손잡이 15g 을 아무도 못 잡았다).
    auto_applied: list[int] = []
    product_typical_mass_kg: float | None = None   # Stage3 가 확인한 '이 제품의 실제 무게'
    product_identified: str | None = None          # 무엇이라고 판단했는지
    mass_sources: list[str] = []                   # 웹에서 찾은 근거(검색어 → 수치 → 출처)
    mat_revised: list[dict] = []
    if auto_shell and (api_key or oauth_token):
        cand = [i for i, p in enumerate(parts) if _shell_eligible(p)]
        try:
            # web_search=True: 제품을 알아보면 웹에서 실제 스펙 무게를 확인해 기준값으로 쓴다.
            s3 = await _call_llm(_STAGE3_SYS, _stage3_user(parts, cand, context),
                                 api_key, oauth_token, model, images=images, web_search=True)
            try:   # AI 가 확인한 '이 제품의 실제 무게' — 검산 기준값·UI 슬라이더 목표값
                product_typical_mass_kg = float(s3.get("product_typical_mass_kg") or 0) or None
            except (TypeError, ValueError):
                product_typical_mass_kg = None
            product_identified = str(s3.get("product_identified") or "") or None
            _src = s3.get("mass_sources")
            mass_sources = [str(x) for x in _src] if isinstance(_src, list) else (
                [str(_src)] if _src else [])
            by_idx = {int(c["part_index"]): c for c in s3.get("choices", []) if "part_index" in c}
            for i, p in enumerate(parts):
                c = by_idx.get(i)
                if not c:
                    continue
                # (a) 재질 교정 — 너무 가벼운 부품의 유일한 정당한 수단(질량=밀도×부피 유지)
                new_mat = c.get("material")
                if isinstance(new_mat, str) and new_mat in MATERIALS and new_mat != p.get("material"):
                    old_mat, old_mass = p.get("material"), p.get("mass_kg")
                    p["material"] = new_mat
                    p["density"] = float(MATERIALS[new_mat]["density"])
                    p["mass_kg"] = round(p["density"] * float(p["volume_m3"]), 6)
                    if p.get("solid_volume_m3") is not None:
                        p["solid_mass_kg"] = round(p["density"] * float(p["solid_volume_m3"]), 6)
                    p["material_revised_from"] = old_mat
                    mat_revised.append({"part_index": i, "name": p["name"], "from": old_mat,
                                        "to": new_mat, "mass_kg": [old_mass, p["mass_kg"]],
                                        "reasoning": c.get("reasoning", "")})
                    ref = MATERIALS[new_mat]        # 접촉계수도 새 재질 range 중앙값으로
                    for k in ("static_friction", "dynamic_friction", "restitution"):
                        lo, hi = ref[k]
                        p[k] = round((lo + hi) / 2.0, 4)
                # (b) 쉘 보정 — 솔리드 기준값에서 재계산되므로 여러 번 적용해도 누적되지 않는다.
                #     폐합 메시(cand 아님)도 허용한다: 실물은 속 빈 플라스틱 통인데 CAD 가 꽉 찬
                #     원통으로 모델링한 경우가 있고(볼라드 실측: 계산 12.5kg vs 실물 3.5kg),
                #     후보만 허용하면 '너무 무거움'을 판정만 하고 못 고친다. 남용 방지는 프롬프트가 담당.
                wall = float(c.get("wall_mm", 0.0) or 0.0)
                if bool(c.get("hollow")) and wall > 0:
                    p["auto_wall_mm"] = round(wall, 2)
                    p["shell_reason"] = c.get("reasoning", "")
                    apply_shell_to_part(p, wall)
                    auto_applied.append(i)
                else:
                    p.setdefault("shell_reason", c.get("reasoning", ""))
        except Exception as e:  # noqa: BLE001 — 검산 실패해도 계산값으로 진행
            for i in cand:
                parts[i].setdefault("shell_reason", f"(질량 검산 실패: {e})")

    total = round(sum(float(p.get("mass_kg") or 0) for p in parts), 4)
    ratio = (total / product_typical_mass_kg) if product_typical_mass_kg else None
    verdict = None
    if ratio is not None:
        verdict = "너무 가벼움" if ratio < 0.6 else ("너무 무거움" if ratio > 1.6 else "타당")
    return {"clamped": clamped, "auto_shell_applied": auto_applied,
            "product_typical_mass_kg": product_typical_mass_kg,
            # 검산 결과 — 질량을 임의로 덮어쓰지 않고 사람이 판단할 근거로 함께 노출한다.
            "mass_check": {"computed_kg": total, "typical_kg": product_typical_mass_kg,
                           "ratio": round(ratio, 3) if ratio else None, "verdict": verdict,
                           "product_identified": product_identified, "sources": mass_sources},
            "material_revised": mat_revised}


# ───────────────────────── 3. USD 오써링 (UsdPhysics) ─────────────────────────
def _diagonalize_inertia(i_geom_m5: list, density: float) -> tuple[list[float], list[float]]:
    """I = density × I_geom(m^5) 대칭텐서 → (diagonalInertia Vec3, principalAxes quat[w,x,y,z])."""
    I = np.asarray(i_geom_m5, float) * float(density)
    I = (I + I.T) / 2.0
    vals, vecs = np.linalg.eigh(I)  # 오름차순 고유값, 정규직교 고유벡터(열)
    R = vecs
    if np.linalg.det(R) < 0:  # 우수좌표계 보장
        R[:, 0] = -R[:, 0]
    # 회전행렬 → 쿼터니언 (w,x,y,z)
    tr = R[0, 0] + R[1, 1] + R[2, 2]
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        w = 0.25 * s
        x = (R[2, 1] - R[1, 2]) / s
        y = (R[0, 2] - R[2, 0]) / s
        z = (R[1, 0] - R[0, 1]) / s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = math.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
        w = (R[2, 1] - R[1, 2]) / s
        x = 0.25 * s
        y = (R[0, 1] + R[1, 0]) / s
        z = (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = math.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
        w = (R[0, 2] - R[2, 0]) / s
        x = (R[0, 1] + R[1, 0]) / s
        y = 0.25 * s
        z = (R[1, 2] + R[2, 1]) / s
    else:
        s = math.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
        w = (R[1, 0] - R[0, 1]) / s
        x = (R[0, 2] + R[2, 0]) / s
        y = (R[1, 2] + R[2, 1]) / s
        z = 0.25 * s
    return [float(max(v, 0.0)) for v in vals], [float(w), float(x), float(y), float(z)]


_SHELL_KEY = "sr_shell"


def _write_shell_baseline(prim: Any, p: dict[str, Any]) -> None:
    """rigid body prim 에 솔리드 기준값을 메타데이터로 심는다 — 다운로드 때 두께만 받아 패치하기 위함."""
    from pxr import Gf

    diag, _ = _diagonalize_inertia(p.get("solid_inertia_geom_m5", p["inertia_geom_m5"]), p["density"])
    prim.SetCustomDataByKey(_SHELL_KEY, {
        "solidMassKg": float(p.get("solid_mass_kg", p.get("mass_kg") or 0.0)),
        "solidDiag": Gf.Vec3f(float(diag[0]), float(diag[1]), float(diag[2])),
        "areaM2": float(p.get("area_m2", 0.0)),
        "solidVolM3": float(p.get("solid_volume_m3", p["volume_m3"])),
        "eligible": bool(_shell_eligible(p)),
        "autoWallMm": float(p.get("auto_wall_mm", 0.0)),
    })


def render_for_inference(usd_bytes: bytes, file_name: str, views: int = 3,
                         res: int = 640) -> list[tuple[bytes, str]]:
    """추론용 자산 렌더 — 재질/질량 추론이 '실제 생긴 모습'을 보게 한다.

    Isaac RTX 로 여러 각도를 찍어 (jpeg bytes, mime) 리스트로 돌려준다. Isaac 이 없거나
    실패하면 빈 리스트(추론은 이름·기하만으로 진행 — 기존 동작).
    이름·기하만 보면 파란 플라스틱 통을 stainless_steel 로 오판하므로, 텍스처가 있는
    자산에서 특히 효과가 크다. (사용자가 직접 올린 참조 이미지가 있으면 그쪽이 우선)"""
    ext = os.path.splitext(file_name or "")[1].lower()
    if ext not in (".usd", ".usda", ".usdc", ".usdz"):
        return []
    fd, path = tempfile.mkstemp(suffix=ext)
    os.close(fd)
    with open(path, "wb") as f:
        f.write(usd_bytes)
    try:
        from ..material_usd import isaac
        if not isaac.isaac_available():
            return []
        imgs = isaac.render_usd_multiangle(path, views=views, res=res, timeout=600, lights="thumbnail")
        out: list[tuple[bytes, str]] = []
        for _nm, png in imgs:
            try:                      # png → jpeg(용량↓, 토큰↓)
                import io as _io

                from PIL import Image
                im = Image.open(_io.BytesIO(png)).convert("RGB")
                buf = _io.BytesIO()
                im.save(buf, "JPEG", quality=82, optimize=True)
                out.append((buf.getvalue(), "image/jpeg"))
            except Exception:  # noqa: BLE001 — 변환 실패 시 원본 png 사용
                out.append((png, "image/png"))
        return out
    except Exception:  # noqa: BLE001 — 렌더 실패는 치명적이지 않다(이름·기하 추론으로 폴백)
        return []
    finally:
        try:
            os.remove(path)
        except OSError:
            pass



def current_materials(usd_bytes: bytes, file_name: str) -> list[dict[str, Any]]:
    """물성 USD 에서 부품별 (이름, 현재 물리재질, 질량) 을 읽는다 — 사람이 고칠 UI 용.
    물리재질은 author_preserve 가 /World/PhysicsMaterials/<재질키> 로 purpose="physics" 바인딩한 것."""
    from pxr import Usd, UsdGeom, UsdPhysics, UsdShade

    ext = os.path.splitext(file_name or "")[1].lower()
    fd, path = tempfile.mkstemp(suffix=ext if ext in (".usd", ".usda", ".usdc", ".usdz") else ".usd")
    os.close(fd)
    with open(path, "wb") as f:
        f.write(usd_bytes)
    try:
        stage = Usd.Stage.Open(path)
        if stage is None:
            raise RuntimeError("재질 조회: USD 를 열지 못했습니다.")
        out: list[dict[str, Any]] = []
        for prim in stage.Traverse():
            if not prim.IsA(UsdGeom.Mesh):
                continue
            mat = None
            try:
                bound, _rel = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial(materialPurpose="physics")
                if bound and bound.GetPrim().IsValid():
                    mat = bound.GetPrim().GetName()
            except Exception:  # noqa: BLE001
                mat = None
            ma = UsdPhysics.MassAPI(prim) if prim.HasAPI(UsdPhysics.MassAPI) else None
            mass = ma.GetMassAttr().Get() if (ma and ma.GetMassAttr()) else None
            out.append({"name": prim.GetName(), "path": prim.GetPath().pathString,
                        "material": mat, "mass_kg": round(float(mass), 6) if mass else None})
        return out
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def reauthor_materials(usd_bytes: bytes, file_name: str, overrides: dict[str, str],
                       collision: str = "convexHull") -> tuple[bytes, list[dict[str, Any]]]:
    """부품별 재질을 사람이 지정한 값으로 바꿔 물성을 다시 작성(LLM 호출 없음).

    질량 = 밀도 × 부피(형상에서 결정론적 계산), 마찰·반발은 그 재질 range 의 중앙값.
    overrides 키는 부품 prim 경로 또는 이름. 지정 안 된 부품은 **기존 재질을 유지**한다.
    룩(시각 재질·UV·텍스처)은 author_preserve 가 보존한다."""
    import numpy as _np

    parts = parse_geometry(usd_bytes, file_name, "m")
    cur_list = current_materials(usd_bytes, file_name)
    cur_by_name = {c["name"]: c for c in cur_list}
    cur_by_path = {c["path"]: c for c in cur_list}

    applied: list[dict[str, Any]] = []
    for p in parts:
        nm = p.get("name")
        prev = cur_by_name.get(nm) or cur_by_path.get(str(p.get("path", ""))) or {}
        prev_mat = prev.get("material")
        key = overrides.get(nm) or overrides.get(str(p.get("path", ""))) or prev_mat or "steel"
        if key not in MATERIALS:
            key = "steel"
        ref = MATERIALS[key]

        # 기존 질량에는 '속 빈 형상(shell) 보정'이 이미 반영돼 있다. 솔리드 부피로 다시 계산하면
        # 질량이 몇 배로 뛴다 → 기존 질량÷기존밀도 로 '유효 부피'를 되찾아 그걸로 계산한다.
        solid_vol = float(p.get("volume_m3", 0.0) or 0.0)
        eff_vol = solid_vol
        prev_mass = prev.get("mass_kg")
        if prev_mat in MATERIALS and prev_mass:
            v = float(prev_mass) / float(MATERIALS[prev_mat]["density"])
            if v > 0:
                eff_vol = v
        if solid_vol > 0 and eff_vol != solid_vol:
            sc = eff_vol / solid_vol
            p["volume_m3"] = round(eff_vol, 9)
            p["inertia_geom_m5"] = (_np.asarray(p["inertia_geom_m5"], float) * sc).tolist()

        p["material"] = key
        p["density"] = ref["density"]
        p["mass_kg"] = round(float(ref["density"]) * eff_vol, 6)
        p["solid_mass_kg"] = round(float(ref["density"]) * solid_vol, 6)
        p["static_friction"] = round(sum(ref["static_friction"]) / 2, 3)
        p["dynamic_friction"] = round(sum(ref["dynamic_friction"]) / 2, 3)
        p["restitution"] = round(sum(ref["restitution"]) / 2, 3)
        p["material_reasoning"] = "사람 지정" if (nm in overrides) else "기존 유지"
        p["physics_reasoning"] = "range 중앙값(재질 재지정)"
        applied.append({"name": nm, "material": key, "density": ref["density"], "mass_kg": p["mass_kg"],
                        "source": p["material_reasoning"]})
    out = author_preserve(usd_bytes, file_name, parts, "assembled", self_contained=True, collision=collision)
    return out, applied


def reauthor_shell(usd_bytes: bytes, file_name: str, shell_mm: float) -> bytes:
    """이미 작성된 물성 USD 의 rigid body 질량/관성을 두께로 다시 패치(메시·LLM 불필요).

    질량=밀도×부피, 부피_쉘=면적×두께 → 질량·관성은 솔리드 기준값에 sc=vshell/vsolid 만 곱하면 된다.
    shell_mm < 0 → 부품별 AI 자동 두께, 0 → 솔리드(보정 없음), >0 → 모든 적격 부품에 그 두께(mm).
    반환은 자기완결(flatten) .usda."""
    from pxr import Gf, Usd, UsdPhysics

    ext = os.path.splitext(file_name or "")[1].lower()
    fd, path = tempfile.mkstemp(suffix=ext if ext in (".usd", ".usda", ".usdc", ".usdz") else ".usda")
    os.close(fd)
    with open(path, "wb") as f:
        f.write(usd_bytes)
    try:
        stage = Usd.Stage.Open(path)
        if stage is None:
            raise RuntimeError("쉘 재작성: USD 를 열지 못했습니다.")
        for prim in stage.Traverse():
            sr = prim.GetCustomDataByKey(_SHELL_KEY)
            if not sr:
                continue
            solid_mass = float(sr.get("solidMassKg", 0.0))
            sd = sr.get("solidDiag", Gf.Vec3f(0, 0, 0))
            area = float(sr.get("areaM2", 0.0))
            solid_vol = float(sr.get("solidVolM3", 0.0))
            eligible = bool(sr.get("eligible", False))
            auto_wall = float(sr.get("autoWallMm", 0.0))
            t_mm = (auto_wall if eligible else 0.0) if shell_mm < 0 else (shell_mm if (shell_mm > 0 and eligible) else 0.0)
            sc = 1.0
            if t_mm > 0 and solid_vol > 0:
                vshell = area * (t_mm / 1000.0)
                if 0 < vshell < solid_vol:
                    sc = vshell / solid_vol
            ma = UsdPhysics.MassAPI.Apply(prim)
            ma.CreateMassAttr().Set(float(solid_mass * sc))
            ma.CreateDiagonalInertiaAttr().Set(Gf.Vec3f(sd[0] * sc, sd[1] * sc, sd[2] * sc))
        flat = stage.Flatten()
        return flat.ExportToString().encode("utf-8")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


COLLISION_STRATEGIES = ("convexHull", "convexDecomposition", "sdf", "boundingCube")


def _apply_collision(prim, strategy: str = "convexHull") -> None:
    """메시 prim 에 충돌 근사 적용. 전략별:
      convexHull(기본·안정·오목X) / convexDecomposition(안정·오목O, PhysX 분해) /
      sdf(정확·오목O, 단 얇은 고폴리는 폭발 위험) / boundingCube.
    sdf 면 PhysX SDF 스키마+해상도까지 붙인다(없으면 Isaac 이 convexHull 로 폴백)."""
    from pxr import Sdf, UsdPhysics
    UsdPhysics.CollisionAPI.Apply(prim).CreateCollisionEnabledAttr(True)
    approx = strategy if strategy in COLLISION_STRATEGIES else "convexHull"
    UsdPhysics.MeshCollisionAPI.Apply(prim).CreateApproximationAttr(approx)
    if approx == "sdf":
        op = prim.GetMetadata("apiSchemas")
        items = list(op.GetAddedOrExplicitItems()) if op else []
        if "PhysxSDFMeshCollisionAPI" not in items:
            prim.SetMetadata("apiSchemas", Sdf.TokenListOp.CreateExplicit(items + ["PhysxSDFMeshCollisionAPI"]))
        prim.CreateAttribute("physxSDFMeshCollision:sdfResolution", Sdf.ValueTypeNames.Int).Set(256)


def author_usd(parts: list[dict[str, Any]], layout: str = "assembled", collision: str = "convexHull") -> bytes:
    """parts(material/mass/friction 주입됨) → 단일 자기완결 .usda (base UsdPhysics)."""
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdPhysics.SetStageKilogramsPerUnit(stage, 1.0)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())

    scene = UsdPhysics.Scene.Define(stage, "/World/PhysicsScene")
    scene.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))
    scene.CreateGravityMagnitudeAttr(9.81)

    # 지면 (droptest 일 때 정착 확인용)
    ground = UsdGeom.Mesh.Define(stage, "/World/Ground")
    g = 5.0
    ground.CreatePointsAttr([Gf.Vec3f(-g, -g, 0), Gf.Vec3f(g, -g, 0), Gf.Vec3f(g, g, 0), Gf.Vec3f(-g, g, 0)])
    ground.CreateFaceVertexCountsAttr([4])
    ground.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    UsdPhysics.CollisionAPI.Apply(ground.GetPrim())

    mats_scope = UsdGeom.Scope.Define(stage, "/World/PhysicsMaterials")
    seen_mat: dict[str, Any] = {}

    drop_gap = 0.0
    for idx, p in enumerate(parts):
        safe = "".join(ch if ch.isalnum() else "_" for ch in p["name"]) or f"part_{idx}"
        ppath = f"/World/{safe}"
        xf = UsdGeom.Xform.Define(stage, ppath)
        prim = xf.GetPrim()
        UsdPhysics.RigidBodyAPI.Apply(prim)
        mass_api = UsdPhysics.MassAPI.Apply(prim)
        mass_api.CreateMassAttr(float(p["mass_kg"]))
        mass_api.CreateCenterOfMassAttr(Gf.Vec3f(*[float(x) for x in p["com_m"]]))
        diag, quat = _diagonalize_inertia(p["inertia_geom_m5"], p["density"])
        mass_api.CreateDiagonalInertiaAttr(Gf.Vec3f(diag[0], diag[1], diag[2]))
        mass_api.CreatePrincipalAxesAttr(Gf.Quatf(quat[0], quat[1], quat[2], quat[3]))
        _write_shell_baseline(prim, p)

        if layout == "droptest":
            UsdGeom.XformCommonAPI(xf).SetTranslate((drop_gap, 0.0, float(p["dims_m"][2]) + 0.1))
            drop_gap += float(p["dims_m"][0]) + 0.2

        # 메시 (visual + collision 겸용)
        V = np.asarray(p["_V"], float)
        F = np.asarray(p["_F"], int)
        mesh = UsdGeom.Mesh.Define(stage, f"{ppath}/geom")
        mesh.CreatePointsAttr([Gf.Vec3f(float(a), float(b), float(c)) for a, b, c in V])
        mesh.CreateFaceVertexCountsAttr([3] * len(F))
        mesh.CreateFaceVertexIndicesAttr([int(x) for x in F.reshape(-1)])
        mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
        _apply_collision(mesh.GetPrim(), collision)

        # 물리 머티리얼 (재질별 1개 재사용)
        m = p["material"]
        if m not in seen_mat:
            mat = UsdShade.Material.Define(stage, f"/World/PhysicsMaterials/{m}")
            pm = UsdPhysics.MaterialAPI.Apply(mat.GetPrim())
            pm.CreateStaticFrictionAttr(float(p["static_friction"]))
            pm.CreateDynamicFrictionAttr(float(p["dynamic_friction"]))
            pm.CreateRestitutionAttr(float(p["restitution"]))
            seen_mat[m] = mat
        bind = UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim())
        bind.Bind(seen_mat[m], bindingStrength=UsdShade.Tokens.weakerThanDescendants, materialPurpose="physics")

    layer = stage.GetRootLayer()
    return layer.ExportToString().encode("utf-8")


def author_preserve(usd_bytes: bytes, file_name: str, parts: list[dict[str, Any]],
                    layout: str = "assembled", self_contained: bool = False,
                    collision: str = "convexHull") -> bytes:
    """입력 USD(시각 vMaterials MDL 바인딩 포함) 위에 UsdPhysics 를 '얹어' 작성한다.
    author_usd 는 형상을 새로 써서 시각 재질을 버리지만, 이 함수는 입력 stage 를 열어
    기존 메시·재질 바인딩을 그대로 두고 RigidBody/Mass/Collision + 물리 머티리얼(purpose=physics)
    만 추가한다 → 파이프라인(재질→물성)에서 재질이 보존되어 턴테이블에 제대로 나온다.

    메시 매칭은 stage traversal 순서(=parse_geometry 가 쓰는 load_parts 순서)로 parts 와 zip.

    self_contained=True 면 입력의 외부 의존(MDL/텍스처)을 함께 묶어 단일 .usdz 로 반환(입력 temp 가
    살아있는 동안). 외부 의존이 없으면 .usda 텍스트. (구조 보존 카드용 — 경로·룩 유지하면서 자기완결)"""
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade, UsdUtils

    ext = os.path.splitext(file_name or "")[1].lower()
    fd, path = tempfile.mkstemp(suffix=ext if ext in (".usd", ".usda", ".usdc", ".usdz") else ".usd")
    os.close(fd)
    with open(path, "wb") as f:
        f.write(usd_bytes)
    try:
        stage = Usd.Stage.Open(path)
        if stage is None:
            raise RuntimeError("물성 보존 작성: 입력 USD 를 열지 못했습니다.")
        dp = stage.GetDefaultPrim()
        base = dp.GetPath().pathString if (dp and dp.IsValid()) else "/World"
        if not (dp and dp.IsValid()):
            UsdGeom.Xform.Define(stage, "/World")

        UsdPhysics.SetStageKilogramsPerUnit(stage, 1.0)
        scene = UsdPhysics.Scene.Define(stage, f"{base}/PhysicsScene")
        scene.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))
        scene.CreateGravityMagnitudeAttr(9.81)
        UsdGeom.Scope.Define(stage, f"{base}/PhysicsMaterials")
        seen_mat: dict[str, Any] = {}

        meshes = [pr for pr in stage.Traverse() if pr.IsA(UsdGeom.Mesh)]
        for prim, p in zip(meshes, parts):
            UsdPhysics.RigidBodyAPI.Apply(prim)
            ma = UsdPhysics.MassAPI.Apply(prim)
            ma.CreateMassAttr(float(p["mass_kg"]))
            ma.CreateCenterOfMassAttr(Gf.Vec3f(*[float(x) for x in p["com_m"]]))
            diag, quat = _diagonalize_inertia(p["inertia_geom_m5"], p["density"])
            ma.CreateDiagonalInertiaAttr(Gf.Vec3f(diag[0], diag[1], diag[2]))
            ma.CreatePrincipalAxesAttr(Gf.Quatf(quat[0], quat[1], quat[2], quat[3]))
            _write_shell_baseline(prim, p)
            _apply_collision(prim, collision)

            m = p["material"]
            if m not in seen_mat:
                safe = "".join(ch if ch.isalnum() else "_" for ch in m) or "mat"
                mat = UsdShade.Material.Define(stage, f"{base}/PhysicsMaterials/{safe}")
                pm = UsdPhysics.MaterialAPI.Apply(mat.GetPrim())
                pm.CreateStaticFrictionAttr(float(p["static_friction"]))
                pm.CreateDynamicFrictionAttr(float(p["dynamic_friction"]))
                pm.CreateRestitutionAttr(float(p["restitution"]))
                seen_mat[m] = mat
            # 물리 바인딩은 purpose="physics" — 기존 시각(MDL) 바인딩(default purpose)은 건드리지 않음.
            UsdShade.MaterialBindingAPI.Apply(prim).Bind(
                seen_mat[m], bindingStrength=UsdShade.Tokens.weakerThanDescendants, materialPurpose="physics")

        if not self_contained:
            return stage.GetRootLayer().ExportToString().encode("utf-8")
        # 자기완결: workdir 에 export 후 외부 의존(MDL/텍스처)이 있으면 .usdz 로 번들(입력 temp 살아있는 지금).
        wd = tempfile.mkdtemp()
        outp = os.path.join(wd, "physics.usd")
        stage.Export(outp)
        try:
            _l, assets, unres = UsdUtils.ComputeAllDependencies(Sdf.AssetPath(outp))
        except Exception:  # noqa: BLE001
            assets, unres = [], []
        if not list(assets) and not list(unres or []):
            with open(outp, "rb") as f:
                return f.read()   # 외부 의존 없음 → 단일 .usd(crate)
        # 평탄화: 의존을 ./materials 로 풀고 재앵커(중첩 usdz 방지 — 안 하면 MDL 이 tmp.usdz[materials/X.mdl]
        # 로 중첩돼 Isaac 이 셰이더를 못 찾아 룩이 깨진다). 룩 합치기의 _resolve_deps 재사용.
        try:
            from ..look_merge.pipeline import _resolve_deps
            _resolve_deps(outp, wd, [], fetch_remote=True)
        except Exception:  # noqa: BLE001
            pass
        outz = os.path.join(wd, "physics.usdz")
        try:
            UsdUtils.CreateNewUsdzPackage(Sdf.AssetPath(outp), outz)
            with open(outz, "rb") as f:
                return f.read()
        except Exception:  # noqa: BLE001 — 온라인 등으로 묶기 실패 시 .usd 폴백(룩 합치기로 자기완결화 가능)
            with open(outp, "rb") as f:
                return f.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def preview_glb(parts: list[dict[str, Any]]) -> bytes:
    """파트 형상(미터) → 뷰어용 GLB. 재질 없음(물성 카드는 질량/물리에 집중)."""
    import trimesh

    scene = trimesh.Scene()
    for p in parts:
        scene.add_geometry(
            trimesh.Trimesh(vertices=np.asarray(p["_V"], float), faces=np.asarray(p["_F"], int)),
            node_name="".join(ch if ch.isalnum() else "_" for ch in p["name"]) or "part",
        )
    return scene.export(file_type="glb")


def parts_table(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """직렬화 가능한 결과 테이블(메시 데이터·텐서 제외)."""
    return [
        {
            "name": p["name"],
            "material": p.get("material"),
            "confidence": p.get("confidence"),
            "density": p.get("density"),
            "volume_m3": p["volume_m3"],
            "area_m2": p.get("area_m2", 0.0),   # 쉘 두께 미리보기(질량=밀도×면적×두께)용
            "mass_kg": p.get("mass_kg"),
            "solid_mass_kg": p.get("solid_mass_kg", p.get("mass_kg")),   # 솔리드(보정 전) 질량 — 슬라이더 베이스라인
            "solid_volume_m3": p.get("solid_volume_m3", p["volume_m3"]),
            "auto_wall_mm": p.get("auto_wall_mm", 0.0),                  # LLM 자동 추천 벽두께(0=솔리드 판정)
            "shell_eligible": _shell_eligible(p),
            "shell_reason": p.get("shell_reason", ""),
            "bound_visual_material": p.get("bound_visual_material", ""),   # 입력 USD에서 물려받은 외형 재질명
            "dims_mm": p["dims_mm"],
            "volume_method": p["volume_method"],
            "static_friction": p.get("static_friction"),
            "dynamic_friction": p.get("dynamic_friction"),
            "restitution": p.get("restitution"),
            "material_reasoning": p.get("material_reasoning", ""),
            "physics_reasoning": p.get("physics_reasoning", ""),
        }
        for p in parts
    ]


def validate_usd(usd_bytes: bytes) -> dict[str, Any]:
    """usdchecker(ComplianceChecker) — 위반 리스트(빈=통과)."""
    try:
        from pxr import Sdf, Usd, UsdUtils
    except Exception as e:  # noqa: BLE001
        return {"ran": False, "error": str(e), "violations": []}
    fd, path = tempfile.mkstemp(suffix=".usda")
    with os.fdopen(fd, "wb") as f:
        f.write(usd_bytes)
    try:
        checker = UsdUtils.ComplianceChecker(
            arkit=False, skipARKitRootLayerCheck=True, verbose=False
        )
        checker.CheckCompliance(path)
        errs = list(checker.GetErrors()) + list(checker.GetFailedChecks())
        return {"ran": True, "violations": [str(e) for e in errs]}
    except Exception as e:  # noqa: BLE001
        return {"ran": False, "error": str(e), "violations": []}
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
