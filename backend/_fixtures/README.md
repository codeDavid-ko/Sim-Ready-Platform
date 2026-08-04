# 테스트 고정 입력(fixtures)

카드 테스트(`/test-cards`)할 때 **매번 이 파일들을 입력으로 쓴다.** 즉석에서 만든 작은 합성 입력으로만 돌리다 실제 버그(대형/단위/이름중복/무거운 원격참조/defaultPrim 없음)를 놓친 적이 있어, 반복되는 입력 형태를 미리 정리해 둔 것.

각 파일은 **과거 실제 버그 1개 이상을 재현**하도록 골랐다. 새 버그를 발견하면 그 입력을 여기 추가하고 아래 표에 한 줄 적어라.

| 파일 | 크기 | defaultPrim | 메시 | subdiv | 원격참조 | 무엇을 막는가 |
|---|---|---|---|---|---|---|
| `small_clean.usd` | ~2KB | 있음 | 6 | 0 | 0 | 스모크 — 빠르게 "되긴 되나" |
| `medium_materials.usda` | 1.9MB | 있음(Asset) | 6 | 0 | 0 | 전형적 다부품+재질. happy path |
| `usdz_no_defaultprim.usdz` | 0.2MB | **없음** | 11 | 11 | 0 | **턴테이블 hang** — defaultPrim 없는 usdz(PseudoRoot 회전). 카메라 궤도 방식이 멈추지 않고 끝나는지 |
| `large_no_defaultprim.usd` | 46MB | **없음** | 159 | 158 | **7** | 규모(159부품)+defaultPrim 없음+원격참조 동시. 로더/뷰어/분류가 붕괴 없이 처리하는지 |
| `heavy_motor_joints.usd` | 45MB | 있음(Group) | 159 | 158 | **7** | **타임아웃 버그**(무거워 1200s 초과) + 모터 조인트 자동 감지. 프레임 비례 타임아웃·진행바가 맞는지 |
| `step_multipart.step` | 0.4MB | (STEP) | — | — | — | STEP 메시 인제스트 경로(mass-physics·articulation·material-usd). 단위/축 해석 |

## 주의
- 이 폴더와 `../_test_results/`(출력)는 git에 올리지 않는다(대용량 바이너리). `.gitignore` 등록됨.
- 무거운 두 파일(`large_*`, `heavy_*`)은 Isaac/NVIDIA 렌더에서 수 분 이상 걸린다 — 무거운 경로 테스트 때만 사용.
- 원본 출처(참고): `~/Downloads/GIS*.usd`, `Test.usdz`, `GIS_외형도.{usda,STEP}`. 원본이 갱신되면 이 폴더를 다시 채워라.
