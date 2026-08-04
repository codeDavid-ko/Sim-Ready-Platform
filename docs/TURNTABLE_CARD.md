# 턴테이블 영상 렌더 카드 — 구현 설명 (2026-06-09)

> USD를 올리면 Isaac Sim RTX로 **턴테이블 영상(mp4)** 을 자동 렌더하는 카드.
> 회전은 기본, **줌(렌즈)** 과 **모터(조인트 자동 구동)** 는 옵션. 첨부하신 사양/참고로직 그대로 구현.

## 추가/수정한 파일 (5개)
| 파일 | 내용 |
|---|---|
| `backend/scripts/isaac_turntable.py` (신규) | 헤드리스 Isaac RTX 렌더 스크립트(핵심 로직) |
| `backend/src/algo_runner/workflows/material_usd/isaac.py` (수정) | `render_turntable_video(...)` + `turntable_available()` 추가. 기존 `_ISAAC_LOCK`(1인스턴스 직렬화) + `jobs.run`(취소 가능) + ffmpeg/python.bat 재사용 |
| `backend/.../workflows/turntable/{__init__.py, manifest.json, routes.py}` (신규) | 카드. `POST /api/workflows/turntable/render-submit` → 잡 → mp4 에셋 |
| `frontend/src/workflows/turntable/Turntable.tsx` (신규) | 업로드 + 옵션 UI + 진행바/취소 + mp4 미리보기/다운로드 |
| `frontend/src/workflows/registry.ts` (수정) | `"turntable": Turntable` 등록 |

카드는 **기타(etc) 카테고리**에 표시됩니다(아이콘 🎥).

## 동작 흐름
```
USD 업로드 → 옵션 선택 → "영상 생성"(잡 시작)
 → Isaac 헤드리스 RTX: 키프레임 생성 → 프레임마다 렌더(capture) → PNG 시퀀스 → ffmpeg mp4
 → 폴링 완료 → 카드에서 mp4 미리보기 + 다운로드
```
잡 기반이라 **진행바·경과시간·취소** 가 다른 카드들과 동일하게 동작합니다(취소 시 Isaac 프로세스 트리 종료).

## 렌더 스크립트 핵심 (`isaac_turntable.py`) — 사양대로
1. **모터(drive) 조인트 자동 감지** — `collect_motorized_joints()`. `UsdPhysics.Joint` 중 `DriveAPI`(angular/linear)가 붙은 것만 수집. 특정 조인트 하드코딩 없음(일반화). body1(움직이는 쪽)·localPos1(경첩)·axis·limit을 읽음. **물리 제거 전에** 호출.
2. **물리 제거 + Fabric Scene Delegate off** — `strip_physics()`. `useFabricSceneDelegate=False` + `UsdPhysics.Scene`/RigidBody/Collision/Articulation API 제거. (안 하면 USD 트랜스폼 애니가 렌더에 안 나옴.)
3. **턴테이블** — defaultPrim을 bbox 중심·stage up축 기준으로 회전 키프레임(정적 카메라). 바퀴수/방향/길이 옵션.
4. **모터 구동(옵션)** — 각 조인트를 경첩/축 기준 회전(또는 prismatic이면 평행이동) 키프레임. `hold`(0→target 빨리 열고 유지) / `cycle`(0→peak→0 왕복). limit 있으면 그 범위, 없으면 기본각.
5. **줌(옵션)** — 카메라 **FocalLength** 키프레임(렌즈 줌). 카메라는 정적(이동 X — 헤드리스 검은 프레임 방지). t≈0.42 줌인 → 유지 → 0.70 줌아웃.
6. **프레임 캡처** — 타임라인 시각을 프레임마다 진행 → **settle**(RTX 수렴용 다회 update) → `capture_viewport_to_file`. 검은 프레임 방지.
7. **mp4** — ffmpeg로 PNG 시퀀스 stitch (libx264, crf 18).

## 옵션 (셋 다 독립 조합)
| 옵션 | 기본 | 파라미터 |
|---|---|---|
| 턴테이블 회전 | ON | 바퀴수(0.25~5)·방향(CCW/CW)·길이(초) |
| 줌 | OFF | 배율(×1.1~5) |
| 모터/조인트 | OFF | 모드(열고유지/왕복)·기본각 |
| 해상도 | 1080 | 720/1080/1440 |
| FPS | 24 | 12~60 |

## 지킨 규칙 (사양의 "반드시")
- ✅ 모터 하드코딩 금지 → drive 조인트 자동 감지·일반화
- ✅ 물리 제거 + Fabric off (조인트 정보는 제거 **전**에 수집)
- ✅ 카메라 정적 + 렌즈(FocalLength) 줌 (카메라 이동 줌 안 씀)
- ✅ 프레임마다 settle (검은 프레임 방지), 캡처는 `capture_viewport_to_file`
- ✅ Isaac 1 인스턴스 직렬화(`_ISAAC_LOCK`) + 헤드리스 부팅/캡처는 기존 `isaac_render.py` 패턴

## 테스트
drive 조인트(문)가 있는 합성 캐비닛 USD로 **turntable+zoom+motor 전부 켜고** 실제 렌더 검증(720p/1바퀴/4초/왕복문). 결과 mp4: `backend/_test_results/turntable_test.mp4`.
→ 결과는 이 문서 하단 "테스트 결과"에 기록.

## 추가 개선 (요청 반영, 2026-06-09 밤)
1. **입력 자동 정리 토글** (`clean`, 기본 ON) — 렌더 전 USD에서 **거대 환경/바닥 평면을 제거**(재질·조인트 유지). 카메라는 bbox 기준이라 평면만 빼면 절대 스케일과 무관하게 객체가 제대로 프레이밍됨. (원본 GIS처럼 72km 평면 낀 자산도 OK.) 스크립트 `--clean`, `strip_env_planes_inplace()`.
2. **줌 UI 예시** — 줌 켜면 "×N = 중반에 N배 줌인→유지→줌아웃" 설명 + 줌 곡선 미니 일러스트(SVG)를 카드에 표시. (배율 입력에 따라 문구가 갱신됨.)
3. **모터 감지→목록→각도 범위 (2-step)** — 모터 켜고 **"① 모터 조인트 감지"** 버튼 → `POST /detect-joints`(Isaac 없이 pxr로 즉시 감지) → **감지된 조인트 목록**(이름·타입·축·limit) 표시 → 각 조인트의 **구동 각도(°)** 를 사용자가 지정 → 렌더에 `motor_targets`(JSON {prim_path: deg})로 전달, 스크립트가 조인트별 override. (감지 안 하면 자동감지+limit/기본각.)

### ★ 추가로 잡은 핵심 버그 — defaultPrim 변환 보존
- 증상: **원본 GIS(e437)** 가 빈 화면. 진단해보니 평면(`/Plane`)은 defaultPrim(`/Group`) 밖이라 프레임과 무관했고(defaultPrim bbox는 정상 2m), 진짜 원인은 **턴테이블 회전 코드가 `ClearXformOpOrder()`로 defaultPrim의 기존 배치/스케일 변환(M0)을 날려** 객체가 엉뚱한 스케일로 튄 것. (합성·클린GIS는 M0=항등이라 안 터졌음.)
- 수정: M0을 읽어두고 회전 op = **M0 × R_center** 로 설정(기존 배치 보존 + 월드중심 회전). 모터 바디는 원래 Wb로 재구성하므로 영향 없음.

### 큰 파일 테스트 결과 (전부 ✅)
- **클린 GIS(146메시, 1080p)**: 24초, 캐비닛 전체 회전·줌·디테일 정상 (`turntable_large.mp4`, `tt_large_montage.png`).
- **원본 GIS(e437, 72km 평면+배치변환, 159메시)**: M0 보존 수정 후 정상 — 제어판/문까지 회전 (`turntable_e437.mp4`, `tt_e437_montage.png`). 36초.
- **합성 캐비닛(drive 조인트)**: 회전+줌+모터(문 열림) (`turntable_test.mp4`).
- **detect-joints**: `door_joint(revolute, Z, limit 0~95°)` 정확 감지.

## 알려진 튜닝/리스크
- **타임라인 스텝**: 헤드리스에서 시간샘플을 프레임별로 렌더하려고 `omni.timeline.set_current_time()` + settle로 진행. 환경에 따라 시간 반영이 안 되면(애니 정지) 폴백으로 "프레임마다 트랜스폼 직접 세팅"으로 바꾸면 됨(스크립트 구조상 쉬움).
- **시간/타임아웃**: 2바퀴·1440p·긴 길이면 오래 걸림. 잡 타임아웃 40분, `settle` 낮추면(예 10) 빨라짐.
- **defaultPrim 회전**: 회전 키프레임을 defaultPrim의 xformOp로 얹음(참고로직과 동일). defaultPrim에 기존 배치 트랜스폼이 있으면 ClearXformOpOrder로 초기화되니, 배치가 중요한 USD는 확인 필요(대부분 Group은 identity).

## 테스트 결과 — ✅ PASS (실렌더 검증)
drive 조인트(문) 있는 합성 캐비닛 USD로 **turntable + zoom + motor 전부 켜고** 실제 렌더:
- 결과 mp4: `backend/_test_results/turntable_test.mp4` (720p, 97프레임, 4.0초, h264) — 정상 생성.
- 프레임 검사(`_test_results/tt_f12/f60/f84.png`):
  - **회전 ✓** — f12(정면) vs f84(다른 각도)로 객체가 수직축 기준 회전.
  - **모터 ✓** — f84에서 **문이 열려 있음**(drive 조인트 자동 감지 → 경첩 기준 회전 애니).
  - **줌 ✓** — f60에서 렌즈 줌인 상태(FocalLength 키프레임).
  - 카메라가 bbox를 제대로 프레이밍(그리드 바닥 위 캐비닛).
- 렌더 시간 ~18초(이 머신 RTX PRO 4500, 720p/97프레임). 고해상도·다바퀴면 더 걸림.

### 구현 중 잡은 버그(메모)
- 초기엔 프레임이 균일 회색(객체 안 보임)이었음 → 원인: **카메라/라이트를 회전하는 defaultPrim 아래에 둬서** 객체와 함께 돌아 화면 밖으로 나갔고, 손으로 짠 lookAt 행렬도 불안정.
- 수정: 라이트/카메라 리그를 **defaultPrim 밖(`/_TtRender`)** 으로 분리 + 조준은 검증된 `ViewportCameraState.set_target_world/set_position_world` 사용. → 정상.
- 타임라인 시간샘플 스텝(`omni.timeline.set_current_time` + settle)은 이 환경에서 정상 동작(회전/문 애니가 프레임에 반영됨).
