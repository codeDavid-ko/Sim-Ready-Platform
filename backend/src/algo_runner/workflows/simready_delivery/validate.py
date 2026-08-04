"""Stage C — simready-validate 서브프로세스 호출 + 리포트 JSON 파싱.

리포트 최상위는 에셋경로 키 → {features_summary, profile_id, profile_version}.
features_summary[FEATURE] = {passed: bool, "failing requirements": "['CODE', ...]"(문자열화 리스트), version}.
요구코드 단위 실패는 여기서만 나온다(콘솔 로그에도 있으나 JSON 이 안정적).
"""
from __future__ import annotations

import ast
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from . import env

# 요구코드 → (한 줄 의미, 자동수정 가능, 수정분류, 해결방법). SPEC v2 §5.1 / v5 §13.
#   AUTO=결정적 자동수정 · STRUCT=형상/레이아웃 재구성(미구현=수동) · INPUT=사용자값 필요.
CODE_INFO: dict[str, tuple[str, bool, str, str]] = {
    "UN.007": ("metersPerUnit ≠ 1 (단위가 m 가 아님)", True, "INPUT", "metersPerUnit=1로 두고 메시·조인트localPos·그래스프·extent 좌표를 파일 단위(예 mm=×0.001)로 스케일해 m로 변환"),
    "UN.002": ("upAxis 미설정/단위", True, "INPUT", "metersPerUnit=1·upAxis=Z 설정 + 단위 스케일"),
    "UN.001": ("metersPerUnit 미설정", True, "AUTO", "metersPerUnit=1 설정"),
    "UN.005": ("timeCodesPerSecond 미설정", True, "AUTO", "timeCodesPerSecond=24 설정"),
    "UN.006": ("defaultPrim 미설정", True, "AUTO", "최상위 루트 프림을 defaultPrim으로 지정"),
    "HI.004": ("defaultPrim 미설정 (stage-has-default-prim)", True, "AUTO", "최상위 루트 프림을 defaultPrim으로 지정(이게 없으면 물리·그래스프 검사가 통째로 스킵됨)"),
    "AA.001": ("에셋 경로(MDL/텍스처/레퍼런스)가 앵커드(./)가 아님/절대경로", True, "AUTO", "MDL은 ./materials/, 텍스처는 ./textures/로 복사하고 경로를 앵커(@./...@)로 변경"),
    "VM.MDL.001": ("MDL sourceAsset 누락/절대경로/파일없음", True, "AUTO", "참조된 .mdl을 ./materials/로 복사 + sourceAsset을 ./로 앵커(파일 실존 보장)"),
    "VM.TEX.001": ("텍스처 > 16384px (다운스케일 필요)", True, "AUTO", "16384px 초과 텍스처를 비율 유지하며 16384 이하로 다운스케일(Pillow)"),
    "VM.TEX.002": ("텍스처 컬러스페이스 오류(albedo=sRGB / 나머지=raw)", True, "AUTO", "albedo/color 텍스처는 sRGB, normal/roughness/metallic 등은 raw로 colorSpace 설정"),
    "NP.006": ("필수 메타데이터(customLayerData) 누락", True, "AUTO", "customLayerData에 asset_name/asset_type/source_file/usd_date_generated/SimReady_Metadata 키 추가"),
    "SR.001": ("SimReady 메타데이터 키 누락", True, "AUTO", "customLayerData에 SimReady_Metadata(profile/version 포함) 등 필수 키 추가"),
    "GSP.001": ("그래스프 벡터(grasp_identifier BasisCurves) 없음", True, "AUTO", "grasp_identifier로 시작하는 BasisCurves(≥2점)를 추가하고 머티리얼 바인딩(미지정 시 bbox 중앙 placeholder)"),
    "COL.001": ("PhysX 충돌 근사가 sdf 가 아님", True, "AUTO", "충돌 메시에 physics:approximation='sdf' 설정(+CollisionAPI/MeshCollisionAPI 보장)"),
    "PMT.001": ("충돌체에 물리 머티리얼 미바인딩", True, "AUTO", "물리 머티리얼(마찰/반발)을 만들어 충돌체에 purpose=physics로 바인딩"),
    "VM.MAT.001": ("일부 GPrim 에 머티리얼 미바인딩", True, "AUTO", "머티리얼이 없는 모든 GPrim(그래스프 커브 포함)에 머티리얼 바인딩"),
    "SR.002": ("썸네일(.thumbs) 없음 (패키지 단계 필요)", True, "AUTO", "에셋 형상을 소프트웨어 렌더해 .thumbs/256x256/<usd>.png 생성"),
    "RB.001": ("강체(RigidBody) 코어 누락", True, "AUTO", "메시에 UsdPhysicsRigidBodyAPI + MassAPI(기본 1kg) 적용"),
    "RB.007": ("강체 질량(MassAPI)이 kg로 미지정", True, "AUTO", "강체에 MassAPI 질량(기본 1kg) 지정(정확한 값은 물성 카드)"),
    "RB.MB.001": ("강체가 2개 미만(멀티바디 FET004)", True, "AUTO", "모든 메시에 RigidBodyAPI+질량 적용해 강체 ≥2개로(메시 1개뿐이면 자동 불가)"),
    "ISA.001": ("Isaac 페이로드 분할 레이아웃 아님", False, "STRUCT", "asset.usd(kind=component)+payloads/{meshes,base,physics}.usd로 재구성 필요 — 자동 미구현"),
    "HI.001": ("단일 루트 아님 (v2.0.0 엄격)", False, "STRUCT", "여러 루트를 단일 루트 Xform 아래로 재부모화 필요 — 자동 미구현"),
    "VG.MESH.001": ("비-Mesh/서브디비전 지오메트리 (v2.0.0)", False, "STRUCT", "analytic prim 테셀레이트/서브디비전 해제 필요 — 자동 미구현(손실 가능)"),
    "VG.002": ("extent 누락 (v2.0.0)", False, "STRUCT", "메시 extent 재계산 필요 — 자동 미구현"),
    "VG.014": ("퇴화 토폴로지 (v2.0.0)", False, "STRUCT", "퇴화/중복 면 정리 필요 — 자동 미구현"),
    "VG.025": ("원점 정렬 아님 (v2.0.0)", False, "STRUCT", "지오메트리를 원점으로 재배치 필요(조인트 프레임 주의) — 자동 미구현"),
    "VG.027": ("노멀 누락/오류 (v2.0.0 엄격)", False, "STRUCT", "노멀 재생성 필요 — 자동 미구현"),
    "VG.028": ("노멀 유효성 (v2.0.0)", False, "STRUCT", "노멀 정규화/수정 필요 — 자동 미구현"),
    "VG.029": ("페이스 와인딩 오류 (v2.0.0 엄격)", False, "STRUCT", "면 와인딩 일관화 필요 — 자동 미구현"),
}


def inspect_content(usd_path: Path) -> dict[str, Any]:
    """에셋에 실제로 무엇이 있는지 센다 — 검증기의 '공허한 통과'(해당 프림이 없어 위반도 없음)를 사용자에게 드러내기 위함.
    검증기는 존재하는 프림의 위반만 보므로, 충돌/강체/그래스프가 0개여도 그 피처는 통과한다."""
    try:
        from pxr import Usd, UsdGeom, UsdPhysics, UsdShade
    except Exception:  # noqa: BLE001
        return {}
    from pxr import Sdf
    st = Usd.Stage.Open(str(usd_path))
    if st is None:
        return {}
    c = {"meshes": 0, "materials": 0, "rigid_bodies": 0, "colliders": 0, "grasp_curves": 0,
         "joints": 0, "textures": 0}
    for p in st.Traverse():
        if p.IsA(UsdGeom.Mesh):
            c["meshes"] += 1
        if p.IsA(UsdShade.Material):
            c["materials"] += 1
        if p.HasAPI(UsdPhysics.RigidBodyAPI):
            c["rigid_bodies"] += 1
        if p.HasAPI(UsdPhysics.CollisionAPI):
            c["colliders"] += 1
        if p.GetName().startswith("grasp_identifier"):
            c["grasp_curves"] += 1
        if "Joint" in str(p.GetTypeName()):
            c["joints"] += 1
        if p.IsA(UsdShade.Shader):   # 텍스처 입력(asset 타입, MDL 모듈 경로 제외)
            for a in p.GetAttributes():
                if a.GetName().startswith("inputs:") and a.GetName() != "info:mdl:sourceAsset" \
                        and a.GetTypeName() == Sdf.ValueTypeNames.Asset and a.Get() is not None:
                    c["textures"] += 1
    return c


_DEFORMABLE_HINTS = ("deformable", "particle", "cloth", "fem", "softbody", "tendon", "hair")


def classify_asset(usd_path: Path) -> dict[str, Any]:
    """SPEC v4 §12 입력 게이트 — 에셋 종류 분류 + 납품 가능 여부.
    변형체(cloth/softbody/FEM/particle)는 SimReady 프로파일이 없어 'block'(납품 불가).
    드라이브가 걸린 관절(로봇)은 'warn'(프롭 파이프라인 범위 밖). 그 외는 rigid/multibody."""
    try:
        from pxr import Usd, UsdGeom, UsdPhysics
    except Exception:  # noqa: BLE001
        return {}
    st = Usd.Stage.Open(str(usd_path))
    if st is None:
        return {}
    joints = drives = art_roots = rb = 0
    deformable: set[str] = set()
    for p in st.Traverse():
        schemas = [str(s) for s in p.GetAppliedSchemas()]
        # 미등록 스키마(PhysxSchema 등 — usd-core 에 플러그인 없음)는 GetAppliedSchemas 가 누락할 수 있어
        # apiSchemas 메타데이터(ListOp)를 직접 읽어 보강.
        md = p.GetMetadata("apiSchemas")
        if md is not None:
            for attr in ("prependedItems", "appendedItems", "explicitItems", "addedItems"):
                schemas += [str(s) for s in getattr(md, attr, []) or []]
        tname = p.GetTypeName()
        for s in schemas + [tname]:
            sl = str(s).lower()
            for h in _DEFORMABLE_HINTS:
                if h in sl:
                    deformable.add(str(s))
        if "Joint" in str(tname) or any("Joint" in s for s in schemas):
            joints += 1
        if any("DriveAPI" in s for s in schemas):
            drives += 1
        if any("ArticulationRootAPI" in s for s in schemas):
            art_roots += 1
        if p.HasAPI(UsdPhysics.RigidBodyAPI):
            rb += 1
    if deformable:
        cls, level = "deformable", "block"
        reason = f"변형체 스키마 감지({', '.join(sorted(deformable))}) — SimReady에 해당 프로파일이 없습니다. 납품 불가, NVIDIA DevRel에 문의하세요."
    elif drives > 0 or art_roots > 0:
        cls, level = "robot", "warn"
        reason = "구동 관절/아티큘레이션(로봇) 감지 — 현재 카드는 프롭 파이프라인 전용입니다. 로봇 프로파일은 범위 밖."
    elif joints > 0 and rb >= 2:
        cls, level = "multibody", "ok"
        reason = "다물체(관절) 프롭 — FET004 경로로 납품 가능(조인트 그래프 검증 권장)."
    else:
        cls, level = "rigid", "ok"
        reason = "정적/단일 강체 프롭 — 표준 경로."
    return {"asset_class": cls, "gate_level": level, "reason": reason,
            "joints": joints, "drives": drives, "articulation_roots": art_roots, "deformable": sorted(deformable)}


def content_notes(content: dict[str, Any], profile: str) -> list[str]:
    """프로파일이 기대하지만 실제로 비어있는 내용 → 경고(검증 통과 ≠ 내용 있음)."""
    if not content:
        return []
    notes: list[str] = []
    advisories: list[str] = []
    is_isaac = "Isaac" in profile
    # essential = 비면 검증기가 실제로 fail시키는 것(RB.001/RB.MB.001/GSP.001/VM.MAT.001).
    if content.get("rigid_bodies", 0) == 0:
        notes.append("강체(RigidBody) 0개 — NVIDIA 필수(RB.001 'essential': 에셋은 강체 ≥1). 이대로는 전체 통과 불가.")
    elif content.get("rigid_bodies", 0) < 2 and content.get("joints", 0) > 0:
        # 조인트(가동부)가 있는데 강체가 부족할 때만 경고. 조인트 없으면 멀티바디는 면제(optional).
        tail = " (메시가 1개라 자동으로 못 만듦 — 부품을 2개 이상으로 나누거나 추가해야 함)" if content.get("meshes", 0) < 2 else ""
        notes.append(f"강체 {content.get('rigid_bodies', 0)}개·조인트 있음 — 멀티바디(RB.MB.001)는 ≥2 필요.{tail}")
    if content.get("grasp_curves", 0) == 0:
        notes.append("그래스프 벡터 0개 — '빈 항목 채우기'로 추가 가능. 없어도 납품은 됨(면제, 패키지는 evidence 모드).")
    if not is_isaac and content.get("materials", 0) == 0:
        notes.append("머티리얼 0개 — 외형 재질 없음(FET006 MDL).")
    # advisory = 검증 필수는 아니지만 실제 시뮬엔 권장(검증기는 없어도 통과).
    if content.get("colliders", 0) == 0:
        advisories.append("충돌체(Collision) 0개 — 검증 필수는 아니지만(RB.COL.001 조건부) 실제 충돌 시뮬하려면 권장.")
    return notes + ([("권장: " + a) for a in advisories] if advisories else [])


def _parse_list(s: Any) -> list[str]:
    if isinstance(s, list):
        return [str(x) for x in s]
    if isinstance(s, str):
        try:
            v = ast.literal_eval(s)
            return [str(x) for x in v] if isinstance(v, (list, tuple)) else []
        except (ValueError, SyntaxError):
            return []
    return []


def run_validate(usd_path: Path, profile: str, version: str, stamp: bool = False,
                 report_out: "Path | None" = None) -> dict[str, Any]:
    """검증 실행 → 구조화 결과.

    반환: {passed, profile, version, features:[{feature, passed, failing:[code]}],
           failing_codes:[code], codes:[{code, meaning, fixable, features:[...]}], raw_stdout}
    """
    import os as _os
    # report_out 가 주어지면 그 경로에 검증 리포트를 보존(패키지 동봉용 — PDF Q4/Q5). 없으면 임시 후 삭제.
    keep = report_out is not None
    if keep:
        out = str(report_out)
        Path(out).parent.mkdir(parents=True, exist_ok=True)
    else:
        fd, out = tempfile.mkstemp(suffix=".json")
        _os.close(fd)
    args = [
        str(env.VALIDATE_EXE),
        "--rules-path", str(env.CAPS),
        "--features-path", str(env.FEATURES),
        "--profiles-path", str(env.PROFILES_TOML),
        "--profile", profile, "--version", version,
        "--output", out,
    ]
    if stamp:
        args.append("--stamp-asset-validation")
    args.append(str(usd_path))
    proc = subprocess.run(args, capture_output=True, text=True, env=env.subprocess_env(), cwd=str(env.REPO))
    stdout = (proc.stdout or "") + (proc.stderr or "")

    report: dict[str, Any] = {}
    try:
        report = json.loads(Path(out).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        report = {}
    finally:
        if not keep:
            try:
                _os.remove(out)
            except OSError:
                pass

    node: dict[str, Any] = {}
    for v in report.values():
        if isinstance(v, dict) and "features_summary" in v:
            node = v
            break

    summary = node.get("features_summary") or {}
    passed_map = {fn: bool(fd.get("passed")) for fn, fd in summary.items()}

    def _dep_names(fd: dict[str, Any]) -> list[str]:
        raw = fd.get("dependencies")
        try:
            v = ast.literal_eval(raw) if isinstance(raw, str) else (raw or [])
        except (ValueError, SyntaxError):
            return []
        names: list[str] = []
        for item in v if isinstance(v, (list, tuple)) else []:
            if isinstance(item, dict):
                names.extend(item.keys())
        return names

    deps = {fn: _dep_names(fd) for fn, fd in summary.items()}
    # 보류(precluded): passed=true 지만 의존 피처가 실패/보류라 '평가되지 않은' 것 → 진짜 통과 아님.
    precluded: set[str] = set()
    for _ in range(len(summary) + 1):  # 고정점
        changed = False
        for fn in summary:
            if not passed_map.get(fn) or fn in precluded:
                continue
            for d in deps.get(fn, []):
                if (d in passed_map and not passed_map[d]) or d in precluded:
                    precluded.add(fn); changed = True; break
        if not changed:
            break

    features: list[dict[str, Any]] = []
    code_to_features: dict[str, list[str]] = {}
    for fname, fdata in summary.items():
        failing = _parse_list(fdata.get("failing requirements")) if not fdata.get("passed") else []
        features.append({
            "feature": fname,
            "passed": bool(fdata.get("passed")) and fname not in precluded,
            "precluded": fname in precluded,
            "failing": failing,
        })
        for c in failing:
            code_to_features.setdefault(c, []).append(fname)

    _DEF = ("(미등록 규칙)", False, "STRUCT", "원본/형상을 직접 확인해 수정 필요 — 자동 미지원")
    content = inspect_content(usd_path)
    codes = [{
        "code": c,
        "meaning": CODE_INFO.get(c, _DEF)[0],
        "fixable": CODE_INFO.get(c, _DEF)[1],
        "fixclass": CODE_INFO.get(c, _DEF)[2],
        "how": CODE_INFO.get(c, _DEF)[3],
        "optional": False,
        "features": fl,
    } for c, fl in sorted(code_to_features.items())]
    # RB.MB.001(멀티바디 ≥2): 조인트가 없으면 NVIDIA 가 FET004 를 'optional' 로 표기 → 면제(optional) 처리.
    # 조인트는 있는데(가동부) 메시가 1개뿐인 비정상은 STRUCT(수동).
    for cc in codes:
        if cc["code"] == "RB.MB.001":
            if content.get("joints", 0) == 0:
                cc.update(fixable=False, fixclass="OPTIONAL", optional=True,
                          how="조인트가 없어 멀티바디 불필요 — NVIDIA 가 FET004 를 'optional' 로 표기. 면제 처리(패키지는 evidence 모드로 빌드).")
            elif content.get("meshes", 0) < 2:
                cc.update(fixable=False, fixclass="STRUCT",
                          how="가동부(조인트)가 있는데 강체 메시가 1개뿐 — 부품을 2개 이상으로 나누거나 추가해야 함(자동 불가).")
        # GSP.001(그래스프): 위 '빈 항목 채우기' 버튼이 담당하므로 자동해결 목록에선 빼고(fixable=False),
        # 없어도 납품은 가능하게 면제(optional) 처리 — 패키지는 evidence 모드(약한 conformance)로 빌드.
        if cc["code"] == "GSP.001":
            cc.update(fixable=False, fixclass="OPTIONAL", optional=True,
                      how="그래스프는 위 '빈 항목 채우기(그래스프)' 버튼으로 추가. 없어도 납품 가능(면제 — 패키지는 evidence 모드). 실제 파지가 필요하면 추가 권장.")

    passed = bool(node) and all(f["passed"] for f in features) and not codes
    # deliverable: 실제 미충족이 'optional(면제)' 코드뿐이면 납품 가능으로 본다(패키지는 evidence 모드).
    hard = [c for c in codes if not c["optional"]]
    deliverable = bool(node) and not hard
    return {
        "passed": passed,
        "deliverable": deliverable,               # passed 거나, 남은 게 optional(면제) 뿐
        "profile": profile,
        "version": version,
        "features": features,
        "failing_codes": sorted(code_to_features.keys()),
        "codes": codes,
        "content": content,                       # 실제 내용 개수(메시/머티리얼/강체/충돌체/그래스프)
        "content_notes": content_notes(content, profile),  # 비어있는데 통과한 항목 경고
        "raw_stdout": stdout[-4000:],
        "ran": bool(node),
    }
