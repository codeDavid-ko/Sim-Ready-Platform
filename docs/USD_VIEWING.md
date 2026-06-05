# Sim-Ready Platform — USD 뷰잉 방식 (핸드오프 문서)

이 문서는 이 플랫폼이 **브라우저에서 USD(재질/물리 결과물)를 어떻게 보여주는지**를
다른 작업자(또는 다른 Claude)에게 그대로 넘기기 위한 설명서다. 코드 기준이며 파일
경로·함수 시그니처·엔드포인트를 그대로 적는다.

## 0. 한 줄 요약

USD를 직접 브라우저에 렌더하는 표준 뷰어는 없다. 대신 **3가지 경로**를 쓴다:

| 방법 | 무엇 | 재질 정확도 | 인터랙션 | 비용 |
|---|---|---|---|---|
| ① model-viewer GLB | 형상+재질을 PBR 근사 GLB로 변환 → `<model-viewer>` | 근사(색/메탈릭/러프니스) | 마우스 궤도 즉시 | 즉시 |
| ② Isaac RTX 턴테이블 | 결과 USD를 Isaac Sim RTX로 360° 렌더 → mp4 | **실제**(MDL/MaterialX) | 영상(비인터랙티브) | ~1–2분 |
| ③ SpinViewer | Isaac RTX 360° **프레임**을 드래그로 회전 | **실제** | **마우스 드래그 회전** | ~2–4분 |

핵심 원리: **"실제 재질로 보이는 RTX 렌더"는 브라우저가 직접 못 하므로, Windows의
Isaac Sim 6.0에서 미리 렌더한 이미지/영상을 브라우저로 가져와 보여준다.** GLB는 그
사이를 메우는 즉석 근사 뷰어다.

---

## 1. 공통 전제

- 결과물(USD/USDZ/GLB/mp4/png)은 모두 `storage.register_asset(...)`로 저장되고
  `download_url = /api/workflows/{workflow_id}/assets/{asset_id}/download` 로 받는다.
  - 저장 위치: `~/.algo-runner/assets/{asset_id}/{filename}`,
    조회: `storage.asset_path(asset_id)` (backend/src/algo_runner/workflows/storage.py)
- **모든 에셋 다운로드는 인증(Bearer 토큰)이 걸려 있다.** 그래서 프론트엔드는 `<img src>`
  /`<video src>`에 보호 URL을 직접 못 박고, **fetch + Authorization 헤더 → Blob →
  `URL.createObjectURL`** 로 object URL을 만들어 쓴다.
  - 헬퍼: `frontend/src/lib/api.ts` 의 `blobUrl(path)`, `downloadFile(path, name)`.
  - 토큰: `frontend/src/lib/auth.ts` 의 `authHeaders()`.
- 긴 작업(렌더)은 **잡 제출 + 폴링** 패턴: `submitAndPoll(path, FormData)` →
  POST가 `{job_id}` 반환 → `GET /api/workflows/jobs/{job_id}` 를 폴링해
  `{status, result}` 회수. (api.ts / backend `workflows/jobs.py`)

---

## 2. 방법 ① model-viewer GLB (PBR 근사, 즉시 인터랙티브)

브라우저에서 바로 도는 유일한 경로. 형상 + 재질 배정을 **PBR 근사 GLB**로 만들어
Google `<model-viewer>` 웹컴포넌트로 띄운다.

- 백엔드 GLB 빌더: `backend/src/algo_runner/workflows/preview.py`
  - `glb_from_assignment_parts(parts, assignment)` — material-usd 결과용.
  - `glb_from_usd_with_bindings(usd_bytes, bindings)` — content-agents 결과용
    (입력 지오메트리 + 부품→재질 바인딩으로 근사).
  - 내부: trimesh `PBRMaterial`(baseColorFactor 0–255 RGBA, metallicFactor,
    roughnessFactor). 색/메탈릭/러프니스는 재질 이름 휴리스틱(`_heuristic_pbr`)으로 추정.
- 프론트엔드: 각 카드가 `import("@google/model-viewer")` 후
  `<model-viewer src={glbObjectUrl} camera-controls auto-rotate ... />`.
  GLB는 `blobUrl(asset.download_url)`로 받아 objectURL로 넣는다.
- 한계: **실제 MDL/MaterialX/생성 텍스처가 아니라 PBR 근사**다. 정밀 룩은 ②③.

---

## 3. 방법 ② Isaac Sim RTX 턴테이블 mp4 (실제 재질, 영상)

결과 USD를 **Windows 네이티브 Isaac Sim 6.0**으로 헤드리스 RTX 렌더해 360° 회전
mp4를 만든다. 이게 "진짜 재질"이 보이는 경로다.

- 러너: `backend/src/algo_runner/workflows/material_usd/isaac.py`
  - `isaac_available()` — python.bat + 스크립트 존재 확인.
  - `render_turntable(usd_path, frames=48, res=720)` → mp4 bytes.
  - 경로 상수:
    - `_PYTHON_BAT = C:\Omniverse\IsaacSim\IsaacSim\_build\windows-x86_64\release\python.bat`
    - `_SCRIPT = backend/scripts/isaac_render.py`
  - ffmpeg는 `_find_ffmpeg()`로 탐색(winget `Gyan.FFmpeg` 포함).
- 렌더 스크립트: `backend/scripts/isaac_render.py`
  - `SimulationApp({"headless": True, "renderer": "RaytracedLighting", width, height})`
  - USD open → `DistantLight(3000)` + `DomeLight(800)` 추가(MDL은 광원 필요).
  - 에셋 bbox로 카메라 반경 계산 → 원 둘레로 N개 각도.
  - **카메라 API 순서 주의**: `ViewportCameraState.set_target_world(...)` →
    그 다음 `set_position_world(...)`.
  - 각 각도 `capture_viewport_to_file` → `view_<i>.png`.
  - `--mp4 --ffmpeg --fps` 주면 ffmpeg가 `view_%d.png` → mp4(libx264, yuv420p, faststart).
- 엔드포인트: 각 카드 라우터의 `POST /api/workflows/{id}/render-submit`
  (form: `asset_id`, `frames`) → 잡 → `{video: assetRecord}`.
- 프론트: 카드의 "Omniverse로 렌더" 버튼 → `submitAndPoll(.../render-submit, fd)` →
  `blobUrl(video.download_url)` → `<video controls autoPlay loop muted>`.

### 3-1. 자기완결성(중요)
Isaac이 Windows에서 USD를 열려면 **자기완결 파일**이어야 한다.
- `material-usd` 출력: 이미 자기완결 USDA(vMaterials MDL을 절대경로로 참조) → 그대로 렌더.
- `content-agents` 출력: WSL 서브레이어 + MaterialX 텍스처 참조라 **비자기완결** →
  WSL에서 `UsdUtils.CreateNewUsdzPackage(...)`로 **USDZ로 번들**(형상+MaterialX+텍스처)
  → Windows로 복사 → Isaac이 USDZ를 열어 MaterialX→MDL로 렌더.
  (그래서 content 카드는 `usdz_asset`을 따로 등록하고, 렌더는 그 usdz id로 돈다.)

---

## 4. 방법 ③ SpinViewer (실제 재질 + 마우스 드래그 회전) — "옴니버스처럼"

②와 같은 RTX 렌더를 쓰되 mp4 대신 **개별 프레임**을 받아, 브라우저에서 마우스
드래그로 프레임을 넘겨 회전시키는 object-movie 뷰어. **실제 재질 + 인터랙션**을 동시에.

- 백엔드 함수: `isaac.render_spin_frames(usd_path, frames=36, res=540)` → `list[bytes]`
  (각도 순서대로 정렬 — `view_<i>.png`의 i를 **숫자**로 정렬해야 함. 사전식이면
  view_10 < view_2 로 꼬임. 이 버그를 `_idx()`로 처리.)
- 공용 엔드포인트(모든 카드 공유): `POST /api/workflows/spin-submit`
  (form: `asset_id`, `frames=36`, `res=540`) → 잡 → `{frames: [download_url...], count}`.
  - 정의: `backend/src/algo_runner/api_workflows.py`. `/{workflow_id}/run`보다 **먼저**
    등록해야 리터럴 경로가 우선 매칭됨.
  - 프레임은 `workflow_id="spin"`으로 등록되지만, 다운로드는 asset_id만 쓰므로 문제없음.
- 프론트 컴포넌트: `frontend/src/workflows/SpinViewer.tsx`
  - props: `{ assetId, label? }`. 자체 "생성" 버튼 → `submitAndPoll("/api/workflows/spin-submit", fd)`.
  - 각 프레임을 `blobUrl()`로 objectURL화 → 배열 보관(언마운트 시 revoke).
  - `onPointerDown/Move/Up`: 가로 드래그 7px당 1프레임, 360° 래핑 → `<img src={frames[idx]}>` 교체.
  - 카드에 `<SpinViewer assetId={usdz_asset.id 또는 asset.id} />` 한 줄로 삽입.

---

## 5. 어떤 USD를 보여주나 (카드별)

- `material-usd` (NdotLight): 자기완결 USDA → GLB 근사 + render-submit(turntable) + SpinViewer(asset).
- `content-material/physics/texture` (NVIDIA): 출력 USD + **usdz_asset**(번들) →
  GLB 근사(바인딩) + render-submit/Spin은 **usdz_asset** 사용.
- `mass-physics` (NdotLight ndotsim): UsdPhysics .usda → 형상 GLB(재질 없음) +
  render-submit/Spin(asset). 물리 카드라 재질보다 형상/물리값이 핵심.

---

## 6. 자주 막히는 곳 (체크리스트)

- 보호 URL을 `<img>/<video>`에 직접 넣으면 401 → 반드시 `blobUrl()` 경유.
- Isaac이 안 보이면 `isaac_available()` 먼저 확인(python.bat 경로/스크립트 존재).
- content 결과가 핑크/검정 → 자기완결 아님. usdz 번들로 렌더하는지 확인.
- 프레임 순서 꼬임 → `view_<i>` 숫자 정렬.
- 렌더 0장/실패 → 광원 추가됐는지, 카메라 set_target→set_position 순서, bbox 반경.
- 긴 렌더로 프록시 타임아웃 → 동기 요청 금지, 반드시 잡+폴링.
- WSL↔Windows 경로: `_to_wsl()` (`C:\X` → `/mnt/c/X`). Bash 툴은 /mnt/c 못 보니 WSL은
  PowerShell에서 `wsl -d Ubuntu-24.04 ...`로 호출.

---

## 7. 핵심 파일 인덱스

- `backend/src/algo_runner/workflows/preview.py` — PBR GLB 빌더(①)
- `backend/src/algo_runner/workflows/material_usd/isaac.py` — Isaac 러너(②③)
- `backend/scripts/isaac_render.py` — Isaac Sim 헤드리스 RTX 렌더 스크립트
- `backend/src/algo_runner/api_workflows.py` — `/spin-submit`(③), 에셋 다운로드, 잡 상태
- `backend/src/algo_runner/workflows/*/routes.py` — 카드별 `/render-submit`(②)
- `frontend/src/workflows/SpinViewer.tsx` — 드래그 회전 뷰어(③)
- `frontend/src/lib/api.ts` — `blobUrl/downloadFile/submitAndPoll`
- 각 카드 `*.tsx` — `<model-viewer>`(①) + 렌더/스핀 버튼
