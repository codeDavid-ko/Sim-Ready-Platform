"""ndotsim 물성 reference 가드레일 (Notion 사양 6절 그대로).

Stage1 은 이 taxonomy 에서만 재질을 고르고, Stage2 접촉계수는 여기 range 로
clamp/검증된다. density 는 추론이 아니라 이 reference 스칼라.

2026-08 스펙 동기화: 12종 → **33종**. Notion "ndotsim 파이프라인 §6 Material reference
가드레일" 표를 그대로 옮긴 값이며(임의값 아님), 프롬프트·enum·검증·UI 드롭다운은 모두
이 dict 에서 파생되므로 **행 추가만으로 확장**된다.

2026-08 웹 검증 후 갱신 → **35종**. 33종 전부를 공개 엔지니어링 자료와 대조했고
(플라스틱 11종은 ISO 1183 표와 소수점까지 일치, 금속은 표준값) 아래만 손봤다:
  * `zinc` 6600 → **7135(순아연)** + `zinc_diecast` 6600(Zamak) 신설 — 예전엔 합금값만 있었다
  * `porcelain` 2400 **신설** — 별칭이 porcelain→ceramic(알루미나 3800)이라 58% 과대였다
  * `rubber` 1100 → **1200** — 충전 기술고무 중앙(무충전 930 / 일반 1300 / 고충전 1500)
  * `cork` 240 → **200** — 240 은 압착 코르크 전용, 천연 코르크는 150~200
  * `cardboard` 250 유지 — 골판지는 '밀도'가 정의상 애매해 단일값으로 못 맞춘다(주석 참조)
  * glass/ceramic/porcelain 의 restitution 은 실측보다 낮지만 **의도 확인 전까지 유지**
근거·출처는 `Non-joint_Test\재질팔레트_검증.html` 참조.
"""

from __future__ import annotations

# material -> {density(kg/m^3), static_friction[lo,hi], dynamic_friction[lo,hi], restitution[lo,hi]}
MATERIALS: dict[str, dict] = {
    # ── 금속 ────────────────────────────────────────────────────────────────
    "steel":           {"density": 7850,  "static_friction": [0.5, 0.8],   "dynamic_friction": [0.4, 0.6],   "restitution": [0.4, 0.7]},
    "stainless_steel": {"density": 8000,  "static_friction": [0.5, 0.8],   "dynamic_friction": [0.4, 0.6],   "restitution": [0.4, 0.7]},
    "cast_iron":       {"density": 7200,  "static_friction": [0.4, 0.7],   "dynamic_friction": [0.3, 0.5],   "restitution": [0.3, 0.5]},
    "aluminum":        {"density": 2700,  "static_friction": [0.4, 0.7],   "dynamic_friction": [0.3, 0.5],   "restitution": [0.3, 0.6]},
    "brass":           {"density": 8500,  "static_friction": [0.4, 0.6],   "dynamic_friction": [0.3, 0.5],   "restitution": [0.3, 0.5]},
    "copper":          {"density": 8960,  "static_friction": [0.4, 0.6],   "dynamic_friction": [0.3, 0.5],   "restitution": [0.3, 0.5]},
    # 아연은 두 갈래로 나눈다(2026-08 웹 검증): 순아연 7135 vs 다이캐스팅 합금(Zamak) 6600.
    # 예전엔 `zinc` 하나에 6600(=Zamak)만 있어서 순아연 부품이 7.5% 가볍게 나왔다.
    # 실물 프롭의 아연 부품은 대개 다이캐스팅이므로 둘 다 두고 이름으로 구분한다.
    "zinc":            {"density": 7135,  "static_friction": [0.4, 0.6],   "dynamic_friction": [0.3, 0.5],   "restitution": [0.3, 0.5]},
    "zinc_diecast":    {"density": 6600,  "static_friction": [0.4, 0.6],   "dynamic_friction": [0.3, 0.5],   "restitution": [0.3, 0.5]},
    "titanium":        {"density": 4500,  "static_friction": [0.4, 0.6],   "dynamic_friction": [0.3, 0.5],   "restitution": [0.3, 0.5]},
    "magnesium":       {"density": 1800,  "static_friction": [0.4, 0.6],   "dynamic_friction": [0.3, 0.5],   "restitution": [0.3, 0.5]},
    "lead":            {"density": 11340, "static_friction": [0.4, 0.6],   "dynamic_friction": [0.3, 0.5],   "restitution": [0.1, 0.3]},
    # ── 범용·엔지니어링 플라스틱 ─────────────────────────────────────────────
    "abs":             {"density": 1050,  "static_friction": [0.35, 0.5],  "dynamic_friction": [0.25, 0.4],  "restitution": [0.3, 0.5]},
    "polycarbonate":   {"density": 1200,  "static_friction": [0.3, 0.45],  "dynamic_friction": [0.25, 0.4],  "restitution": [0.3, 0.5]},
    "nylon":           {"density": 1150,  "static_friction": [0.25, 0.4],  "dynamic_friction": [0.2, 0.35],  "restitution": [0.3, 0.5]},
    "gf_nylon":        {"density": 1350,  "static_friction": [0.25, 0.4],  "dynamic_friction": [0.2, 0.35],  "restitution": [0.3, 0.5]},
    "polypropylene":   {"density": 905,   "static_friction": [0.25, 0.4],  "dynamic_friction": [0.2, 0.3],   "restitution": [0.4, 0.6]},
    "hdpe":            {"density": 950,   "static_friction": [0.2, 0.35],  "dynamic_friction": [0.15, 0.28], "restitution": [0.4, 0.6]},
    "pom":             {"density": 1410,  "static_friction": [0.2, 0.35],  "dynamic_friction": [0.15, 0.3],  "restitution": [0.4, 0.6]},
    "pet":             {"density": 1380,  "static_friction": [0.25, 0.4],  "dynamic_friction": [0.2, 0.3],   "restitution": [0.3, 0.5]},
    "pvc":             {"density": 1400,  "static_friction": [0.3, 0.5],   "dynamic_friction": [0.25, 0.4],  "restitution": [0.3, 0.5]},
    "peek":            {"density": 1320,  "static_friction": [0.3, 0.4],   "dynamic_friction": [0.25, 0.35], "restitution": [0.4, 0.6]},
    "ptfe":            {"density": 2170,  "static_friction": [0.04, 0.1],  "dynamic_friction": [0.04, 0.1],  "restitution": [0.2, 0.4]},
    # ── 엘라스토머 · 폼 ─────────────────────────────────────────────────────
    # 고무는 배합(카본블랙 충전량)에 따라 930(무충전 NR)~1500(고충전)까지 벌어져 단일값이 없다.
    # 예전 1100 은 낮은 쪽이었다 → 그립·범퍼·볼라드 같은 **충전 기술고무**의 중앙인 1200 으로.
    "rubber":          {"density": 1200,  "static_friction": [0.8, 1.2],   "dynamic_friction": [0.7, 1.0],   "restitution": [0.5, 0.9]},
    "silicone":        {"density": 1200,  "static_friction": [0.7, 1.2],   "dynamic_friction": [0.6, 1.0],   "restitution": [0.3, 0.6]},
    "epdm":            {"density": 1150,  "static_friction": [0.7, 1.1],   "dynamic_friction": [0.6, 0.9],   "restitution": [0.4, 0.7]},
    "foam":            {"density": 50,    "static_friction": [0.5, 0.9],   "dynamic_friction": [0.4, 0.8],   "restitution": [0.1, 0.3]},
    # 천연 코르크는 공기건조 150~200(평균 ~200). 240 은 압착(agglomerated) 코르크에만 해당해
    # 일반 코르크 부품에 20~60% 과대였다 → 평균값으로.
    "cork":            {"density": 200,   "static_friction": [0.5, 0.8],   "dynamic_friction": [0.4, 0.7],   "restitution": [0.2, 0.4]},
    # ── 비금속 구조재 ───────────────────────────────────────────────────────
    "wood":            {"density": 600,   "static_friction": [0.3, 0.6],   "dynamic_friction": [0.25, 0.5],  "restitution": [0.2, 0.4]},
    "mdf":             {"density": 750,   "static_friction": [0.3, 0.5],   "dynamic_friction": [0.25, 0.4],  "restitution": [0.2, 0.4]},
    # ⚠ 미해결: glass/ceramic/porcelain 의 restitution 0.2~0.4 는 물리 실측보다 낮다
    # (유리-유리 매끈 ~0.9, 실측 0.755). 시뮬 안정성을 위해 일부러 감쇠한 것인지 확인 필요 —
    # 확인 전까지 값을 바꾸지 않는다(바꾸면 기존 씬의 튀는 거동이 달라진다).
    "glass":           {"density": 2500,  "static_friction": [0.4, 0.7],   "dynamic_friction": [0.3, 0.5],   "restitution": [0.2, 0.4]},
    # ceramic 3800 은 **공업용 알루미나**(3600~3900) 기준이다. 자기(porcelain)는 2400 이라
    # 예전처럼 porcelain 을 ceramic 으로 별칭 처리하면 58% 과대가 된다 → 별도 키로 분리.
    "ceramic":         {"density": 3800,  "static_friction": [0.4, 0.7],   "dynamic_friction": [0.3, 0.5],   "restitution": [0.2, 0.4]},
    "porcelain":       {"density": 2400,  "static_friction": [0.4, 0.7],   "dynamic_friction": [0.3, 0.5],   "restitution": [0.2, 0.4]},
    # ⚠ cardboard 는 '밀도'가 정의상 애매하다 — 골판지는 대부분 공기라 벌크 30~90,
    # 3mm·500gsm 판재의 실효밀도는 ~165, 얇은 솔리드 판지의 섬유부는 ~700 이다.
    # 250 은 '두께를 가진 판재'로 본 거친 근사값이며, 정확한 상자 무게가 필요하면
    # 사람이 면적×평량(g/m²)으로 직접 넣어야 한다(단일값으로 맞출 수 없는 재료).
    "cardboard":       {"density": 250,   "static_friction": [0.4, 0.6],   "dynamic_friction": [0.3, 0.5],   "restitution": [0.1, 0.2]},
    "fiberglass":      {"density": 1900,  "static_friction": [0.3, 0.5],   "dynamic_friction": [0.25, 0.4],  "restitution": [0.3, 0.5]},
    "carbon_fiber":    {"density": 1600,  "static_friction": [0.3, 0.5],   "dynamic_friction": [0.25, 0.4],  "restitution": [0.3, 0.5]},
}

TAXONOMY = list(MATERIALS.keys())
