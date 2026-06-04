"""ndotsim 물성 reference 가드레일 (Notion 사양 6절 그대로).

Stage1 은 이 12키 taxonomy 에서만 재질을 고르고, Stage2 접촉계수는 여기 range 로
clamp/검증된다. density 는 추론이 아니라 이 reference 스칼라.
"""

from __future__ import annotations

# material -> {density(kg/m^3), static_friction[lo,hi], dynamic_friction[lo,hi], restitution[lo,hi]}
MATERIALS: dict[str, dict] = {
    "steel":           {"density": 7850, "static_friction": [0.5, 0.8],  "dynamic_friction": [0.4, 0.6],  "restitution": [0.4, 0.7]},
    "stainless_steel": {"density": 8000, "static_friction": [0.5, 0.8],  "dynamic_friction": [0.4, 0.6],  "restitution": [0.4, 0.7]},
    "aluminum":        {"density": 2700, "static_friction": [0.4, 0.7],  "dynamic_friction": [0.3, 0.5],  "restitution": [0.3, 0.6]},
    "brass":           {"density": 8500, "static_friction": [0.4, 0.6],  "dynamic_friction": [0.3, 0.5],  "restitution": [0.3, 0.5]},
    "titanium":        {"density": 4500, "static_friction": [0.4, 0.6],  "dynamic_friction": [0.3, 0.5],  "restitution": [0.3, 0.5]},
    "abs":             {"density": 1050, "static_friction": [0.35, 0.5], "dynamic_friction": [0.25, 0.4], "restitution": [0.3, 0.5]},
    "polycarbonate":   {"density": 1200, "static_friction": [0.3, 0.45], "dynamic_friction": [0.25, 0.4], "restitution": [0.3, 0.5]},
    "nylon":           {"density": 1150, "static_friction": [0.25, 0.4], "dynamic_friction": [0.2, 0.35], "restitution": [0.3, 0.5]},
    "rubber":          {"density": 1100, "static_friction": [0.8, 1.2],  "dynamic_friction": [0.7, 1.0],  "restitution": [0.5, 0.9]},
    "glass":           {"density": 2500, "static_friction": [0.4, 0.7],  "dynamic_friction": [0.3, 0.5],  "restitution": [0.2, 0.4]},
    "polypropylene":   {"density": 905,  "static_friction": [0.25, 0.4], "dynamic_friction": [0.2, 0.3],  "restitution": [0.4, 0.6]},
    "hdpe":            {"density": 950,  "static_friction": [0.2, 0.35], "dynamic_friction": [0.15, 0.28], "restitution": [0.4, 0.6]},
}

TAXONOMY = list(MATERIALS.keys())
