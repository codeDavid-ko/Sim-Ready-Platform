# Sim-Ready Platform — 플랫폼 개요 & 워크플로우(카드) 설명

3D 에셋을 **시뮬레이션에 바로 쓸 수 있는(sim-ready) USD**로 만드는 작업들을 "카드(워크플로우)"로 모아 놓은 웹 플랫폼입니다. 사용자는 카드를 골라 파일/텍스트/이미지를 넣고 실행하면, 결과(USD·물성·텍스처)와 브라우저 3D 미리보기를 받습니다.

---

## 1. 한눈에

- **목적**: 형상(STEP/STL/USD) → 재질·물리·텍스처를 입혀 sim-ready USD로. 두 추론 엔진(자체 NdotLight / NVIDIA)을 같은 셸에서 사용·비교.
- **구성**: FastAPI 백엔드(`algo_runner`) + Next.js 프런트엔드. **매니페스트 기반 카드 레지스트리** — 폴더에 `manifest.json`만 추가하면 카드가 늘어납니다.
- **AI 호출**: 분류/추론은 **Claude Max 구독(OAuth 토큰)** 으로 (별도 API 키 과금 없음). 일부 기능만 외부 키 필요(아래 표시).
- **접속**: 아이디/비밀번호 로그인. 관리자는 사용자 관리 + **외부 공개 URL(Cloudflare 터널)** 을 켤 수 있음.

---

## 2. 두 추론 엔진의 철학 차이 (중요)

| | **NdotLight (자체)** | **NVIDIA content-agents** |
|---|---|---|
| 접근 | 형상 실측 + **고정 reference 표** + 구독 Claude 분류 | 멀티뷰 렌더 + **VLM이 직접 추정** |
| 재질 어휘 | 정밀(vMaterials MDL ~2,596종) / 물성 12종 고정 | 일반군(metal·plastic·rubber…) |
| 질량·물성 | **형상 정확 부피 × 고정표 밀도**, 마찰은 표 range로 clamp (결정론적) | VLM이 밀도·질량(bbox×fill_factor)·마찰을 직접 추정 (유연·비결정론적) |
| 강점 | 재현성·일관성, 공학적 정확도 | 한 번의 추론으로 폭넓게, 새 형상에 유연 |

→ 같은 "재질/물리 추론"이라도 **NdotLight=실측·고정표**, **NVIDIA=VLM 추정**. 그래서 "비교" 카드로 둘을 나란히 볼 수 있게 했습니다.

---

## 3. 카드(워크플로우) 전체

상태: ✅ 사용 가능 · 🔒 비활성(조건 필요)

### 🟦 NdotLight Trinix

| 카드 | 한 줄 | 입력 → 출력 | 상태 |
|---|---|---|---|
| **재질 추론 USD** | 형상 → 부품별 vMaterials 재질 USD | 형상(STL/STEP/USD/메시) + 참조 이미지/설명 → 부품별 재질 분류 → 재질 바인딩 자기완결 USD | ✅ |
| **물리 추론 (질량·접촉물리)** | STEP/STL → 정확 질량 + 접촉물리 USD | 형상 + 맥락/이미지 힌트 → 형상에서 정확 질량·관성, Claude로 재질(밀도)·마찰·반발 → UsdPhysics USD | ✅ |
| **텍스처 생성 (Stable Diffusion·로컬)** | 로컬 SD로 텍스처 생성 (무료) | 텍스트 프롬프트 → albedo/normal/roughness PBR 맵 + USD/USDZ | ✅ (로컬 GPU, 키 불필요) |
| **3D 모델링 (Trinix CAD)** | 이미지/텍스트 → Trinix 3D 모델(STEP) | 이미지/텍스트 → Trinix MCP로 부품 단위 3D 빌드 → STEP | 🔒 개발중 (Trinix export 필요) |
| **3D 모델링 → 재질·물성** | Trinix 3D → 부품별 재질·물성 USD | 이미지/텍스트 → Trinix 3D(STEP) → 부품별 재질 + 질량·물성까지 한 번에 | 🔒 개발중 (Trinix export 필요) |

### 🟩 NVIDIA Content Agents

| 카드 | 한 줄 | 입력 → 출력 | 상태 |
|---|---|---|---|
| **재질 추론** | NVIDIA 엔진으로 부품별 재질 추론 | USD → WSL 멀티뷰 렌더 + VLM(구독 Claude) → 재질 바인딩 USD | ✅ |
| **물리 추론** | NVIDIA 엔진으로 질량·물리 추론 | USD → VLM이 밀도·질량·마찰 추정 → UsdPhysics USD | ✅ |
| **텍스처 추론** | NVIDIA 엔진으로 텍스처 생성 | USD → 재질/텍스처 추론·생성 | 🔒 NVIDIA API 키 필요 (텍스처 생성은 diffusion 모델) |

### ⚖️ 비교 (Comparison)

| 카드 | 한 줄 | 설명 | 상태 |
|---|---|---|---|
| **엔진 비교: 재질** | 두 엔진의 재질 추론을 나란히 비교 | 같은 USD를 NdotLight vs NVIDIA 재질 추론에 모두 돌려 부품별 결과 비교 + 양쪽 USD/RTX 뷰 | ✅ |
| **엔진 비교: 물리** | 두 엔진의 물리 추론을 나란히 비교 | 같은 USD를 두 물리 엔진에 돌려 부품별 밀도·질량·마찰 대조 + 양쪽 USD/RTX 뷰 | ✅ |

### 🧰 기타 (유틸리티)

| 카드 | 한 줄 | 설명 | 상태 |
|---|---|---|---|
| **형상 → USD 변환 (에셋 준비)** | STEP/STL/메시 → sim-ready USD | STEP/STL/OBJ/GLB/PLY → Z-up·meters·충돌·리지드바디가 부여된 USD로 정규화. 다른 카드에 넣기 전 전처리 | ✅ |

---

## 4. 결과 보기 (모든 재질 카드 공통)

세 가지 뷰어를 제공합니다 (자세한 건 `docs/USD_VIEWING.md`):

1. **PBR 근사 GLB (model-viewer)** — 브라우저에서 즉시 마우스 회전. 색/금속성/거칠기 근사.
2. **Omniverse RTX 턴테이블 (mp4)** — Isaac Sim RTX로 360° 렌더한 실제 재질 영상.
3. **인터랙티브 RTX 뷰어 (SpinViewer)** — Isaac RTX 360° 프레임을 **마우스 드래그로 회전**. Omniverse GUI에서 보는 것과 같은 렌더러·재질 룩(전달만 프레임 방식).

---

## 5. 권장 워크플로우 예시

1. **형상부터**: (STEP/STL이 있으면) "형상 → USD 변환"으로 USD 정규화.
2. **재질**: "재질 추론 USD"(자체) 또는 "재질 추론"(NVIDIA) — 또는 "엔진 비교: 재질"로 둘 다.
3. **물리**: "물리 추론"(자체, 정확 질량) 또는 NVIDIA — 또는 "엔진 비교: 물리".
4. **텍스처**(선택): "텍스처 생성(SD)"(무료·로컬) 또는 NVIDIA(키 필요).
5. **확인**: 각 결과를 RTX 뷰어로 마우스 회전하며 검수, USD 다운로드.

---

## 6. 운영 / 접속

- **로그인**: 아이디/비밀번호. 최초 1회 관리자 계정을 직접 설정.
- **사용자 관리(관리자)**: 사용자 추가·비밀번호 변경·역할(admin/user)·삭제.
- **외부 공개(관리자)**: Cloudflare 임시 터널로 `*.trycloudflare.com` URL 발급(로그인 보호). 끄면 사라지고 다시 켜면 새 주소.
- **AI 비용**: 재질/물리/분류·텍스트는 **Claude Max 구독으로 무료**. 외부 키 필요한 것은 **NVIDIA 텍스처 생성**(diffusion) 뿐.

---

## 7. 현재 상태 / 제한

- **NVIDIA 텍스처 추론**: 텍스처 *생성*은 diffusion(이미지 생성) 모델이라 `NVIDIA_API_KEY`(또는 OpenAI/Google 이미지생성 키) 필요 → 키 없으면 카드 비활성.
- **Trinix 3D 모델링 / 파이프라인**: 모델링 자체는 동작하나 Trinix의 `export_scene`이 현재 서버 측에서 오류(파일 추출 불가) → 카드 비활성(개발중). Trinix export 복구 시 즉시 활성화 가능(코드 완비).
- **NVIDIA content-agents 실행**: WSL2 + GPU 필요(멀티뷰 렌더). 실제 RTX 렌더는 Windows Isaac Sim 사용.

---

## 8. 확장 (개발 메모)

- 새 카드 = `backend/.../workflows/<id>/manifest.json` (+ `handler.py` 또는 `routes.py`) + 프런트 `registry.ts` 한 줄 + `<id>/Component.tsx`. registry가 자동 발견.
- 카드 메타: `category`(ndotlight-trinix / nvidia-content-agents / comparison / etc), `order`(정렬), `tagline`(대시보드 한 줄), `hidden`/`disabled`/`disabledNote`(노출 제어).
