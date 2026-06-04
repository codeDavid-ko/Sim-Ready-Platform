# Sim-ready Platform — 기획안 (v1, 구현 반영본)

> 출처: 기획 핸드오프 `PLANNING_HANDOFF.md` (v0.1, 기획 에이전트 "지우", 2026-06-04).
> 본 문서는 그 핸드오프의 `(미정)`·`(가설)` 항목을 **사용자와 확정**하고, 그 결정대로
> 1차 구현을 끝낸 뒤의 상태를 반영한 기획안이다.
> 라벨: `(확정)` = 사용자 합의·구현됨 / `(가설)` = 방향만 / `(후속)` = 다음 단계.

---

## 0. 한 줄 정의 (확정)
PM이 **현장에서 sim-ready 워크플로우를 즉시 골라 실행**하고, 연구자가 **만든 알고리즘을
본체 수정 없이 빠르게 워크플로우로 꽂을 수 있는**, 속도(speed-to-use) 중심의 모듈형 작업 플랫폼.

핵심 가치 = 속도 두 축: **사용 속도**(PM이 빨리 찾아 실행) + **적용 속도**(연구자 알고리즘이 빨리 올라옴).
Sim-ready 의미 = (a) 시뮬레이션용 3D 에셋 준비 + (c) 합성 데이터 생성. **이번 첫 워크플로우 = (a) 에셋 준비.**

---

## 1. 확정된 결정 (이번 합의)

핸드오프 6장 "열린 질문"에 대한 사용자 답변:

| 항목 | 결정 (확정) |
|---|---|
| **기술 스택** | 기존 `algo-runner-starter` repo(**Next.js 14 + FastAPI**)를 베이스로, 레지스트리/마운트 레이어를 위에 얹음. 본체 거의 재사용. |
| **첫 워크플로우 입력** | **3D 파일 업로드** (.glb/.gltf/.obj/.stl/.ply) |
| **출력** | **변환 USD 파일 다운로드 + 저장소 등록** (+ sim-ready 검증 리포트) |
| **처리 흐름** | **원샷 실행** (중간 승인 단계 없음) |
| **변환 깊이** | **Isaac급 USD 변환** — `usd-core(pxr)` 로 `.usda` 생성 + `UsdPhysics` (RigidBody/Collision/Mass), Z-up·meters 정규화 |
| **git** | `feat/sim-ready-shell` 브랜치에 작업·푸시 |

---

## 2. 설계 원칙 (확정, 핸드오프 계승)
1. **셸은 내용을 모른다** — 셸은 워크플로우 내부 UI/알고리즘을 모른 채 컨테이너로만 띄움.
2. **공통 규약, 자유로운 내부** — 셸은 매니페스트 규약만 정의. 워크플로우 내부는 자유.
3. **추가는 곧 등록** — 워크플로우 추가 = `workflows/` 폴더에 `manifest.json` + `handler.py` 떨굼(백엔드) + 프런트 모듈맵 한 줄. 본체 재배포/재작성 없음.
4. **권한 인식** — 인증은 단순 로그인(현재)에서 향후 워크플로우별 접근제어로 확장 가능하게 매니페스트에 `requiredPermissions` 자리 마련.

---

## 3. 아키텍처 (구현됨)

셸의 역할 3가지 — 핸드오프대로 고정:

1. **인증** (SCR-01): 기존 `auth.py` (비밀번호 1개 게이트 + HMAC 토큰). 공개/비번 모드.
2. **레지스트리** (SCR-02): `GET /api/workflows` 가 매니페스트 목록 반환 → 카드 그리드.
3. **마운트** (SCR-03): 카드 클릭 시 `manifest.entry` 키로 프런트 모듈을 화면에 마운트.

> 단계 권고대로 **풀 마이크로프론트엔드는 채택하지 않음.** "라우트 기반 모듈 + 매니페스트 컨벤션"
> 으로 가볍게 시작 (프런트 `MODULES` 맵 + 백엔드 폴더 자동 발견).

### 백엔드 (FastAPI)
```
backend/src/algo_runner/
  workflows/
    registry.py            # 폴더 자동 발견 → 매니페스트+handler 등록
    storage.py             # 에셋 파일 + registry.json (~/.algo-runner/assets)
    asset_prep/            # manifest.json + handler.py (3D→USD)
    sample_sum/            # manifest.json + handler.py (기존 run_algorithm 래핑)
  api_workflows.py         # GET /api/workflows, POST /{id}/run, GET .../download
  main.py                  # 라우터 등록 (기존 /api/run 은 그대로 유지)
```

### 프런트 (Next.js)
```
frontend/src/
  app/page.tsx                       # 셸: 로그인 / 그리드 / 컨테이너 분기
  components/CardGrid.tsx            # SCR-02
  components/WorkflowContainer.tsx   # SCR-03 (entry 키로 모듈 마운트)
  workflows/registry.ts             # entry → 컴포넌트 맵 (MODULES)
  workflows/asset-prep/AssetPrep.tsx
  workflows/sample-sum/SampleSum.tsx
```

### 워크플로우 ↔ 셸 계약
- 셸 → 모듈(props): `manifest`, `onBack`(네비게이션 핸들). 향후 사용자/권한/결과 콜백 확장 여지.
- 모듈 → 셸: 매니페스트 준수 + `entry` 컴포넌트 노출.
- 핸들러 → 셸: `run(params, file_bytes, file_name, ctx)`; `ctx.register_asset(...)` 로 결과물 등록.

---

## 4. 매니페스트 규약 v1 (확정 — 코드와 일치)

```jsonc
{
  "id": "asset-prep",                 // 고유 ID (= 프런트 모듈 키와 매칭)
  "name": "에셋 준비",                 // 카드 표시 이름
  "description": "3D 에셋을 sim-ready USD로 변환·검증·등록",
  "icon": "📦",
  "version": "1.0.0",
  "requiredPermissions": [],          // 향후 워크플로우별 권한 (자리만)
  "entry": "asset-prep",              // 프런트 MODULES 맵 키
  "io": {
    "input":  { "file": { "accept": ".glb,.gltf,.obj,.stl,.ply", "required": true } },
    "output": ["download", "register", "report"]
  }
}
```

---

## 5. 첫 워크플로우 — 에셋 준비 (구현됨)

- **입력**: 3D 메시 파일 업로드.
- **처리(원샷)**: `trimesh` 로드 → glTF는 Y-up→Z-up 회전 → 바운딩박스 중심 원점 정렬
  → `usd-core` 로 `.usda` 저작 (`UsdGeom.Mesh` + `UsdPhysics` RigidBody/Mass/Collision[convexHull], upAxis=Z, metersPerUnit=1).
- **출력**: ① 변환 `.usda` 다운로드 ② 저장소 등록(`~/.algo-runner/assets` + `registry.json`)
  ③ sim-ready 검증 리포트(단위/축/정렬/충돌/리지드바디/폐쇄성/삼각형 예산/스케일).

---

## 6. MVP 범위

**In scope (완료):** SCR-01 로그인 · SCR-02 카드 그리드 · SCR-03 컨테이너 · 매니페스트 규약 v1 ·
에셋 준비 워크플로우 1개로 "끼워짐" 실증 (+ sample-sum 으로 N개 등록 실증).

**Out of scope (후속):** 관리자 콘솔 · 워크플로우별 세부 권한 · 합성 데이터 워크플로우 ·
named tunnel 고정 주소 · 에셋 저장소 브라우징 UI.

---

## 7. 후속 과제 (가설/후속)
- 에셋 저장소 목록·재사용 UI (등록된 에셋을 다음 워크플로우 입력으로).
- USD 변환 고도화: 머티리얼/텍스처 보존, 충돌 근사 선택(convexDecomposition 등), 다중 메시 계층.
- 워크플로우별 권한(`requiredPermissions`) 실제 적용.
- 합성 데이터 생성 워크플로우(축 c) 추가.

---

*기획 합의 기준일 2026-06-04 / 핸드오프 v0.1 → 본 기획안 v1 (구현 반영) / 베이스: algo-runner-starter.*
