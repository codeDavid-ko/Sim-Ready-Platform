"use client";

import type { WorkflowManifest } from "@/workflows/registry";

// 카테고리 표시 순서/라벨. 매니페스트의 category 키와 매칭.
const CATEGORIES: { key: string; label: string }[] = [
  { key: "ndotlight-trinix", label: "NdotLight Trinix" },
  { key: "nvidia-content-agents", label: "NVIDIA Content Agents" },
  { key: "comparison", label: "비교 (Comparison)" },
  { key: "etc", label: "기타 (유틸리티)" },
];
const OTHER = { key: "__other__", label: "기타" };

// SCR-02 — 매니페스트 목록을 카테고리별 카드 그리드로 렌더. 셸은 카드 내용을 모른다.
export function CardGrid({
  workflows,
  onSelect,
}: {
  workflows: WorkflowManifest[];
  onSelect: (m: WorkflowManifest) => void;
}) {
  const visible = workflows.filter((w) => !w.hidden);
  if (visible.length === 0) {
    return <p className="muted">등록된 워크플로우가 없습니다.</p>;
  }

  const known = new Set(CATEGORIES.map((c) => c.key));
  const groups = [...CATEGORIES, OTHER]
    .map((c) => ({
      ...c,
      items: visible
        .filter((w) =>
          c.key === OTHER.key ? !w.category || !known.has(w.category) : w.category === c.key,
        )
        .sort((a, b) => (a.order ?? 99) - (b.order ?? 99)),
    }))
    .filter((g) => g.items.length > 0);

  return (
    <div className="cats">
      {groups.map((g) => (
        <section key={g.key} className="cat">
          <h3 className="cat-title">{g.label}</h3>
          <div className="grid">
            {g.items.map((w) =>
              w.disabled ? (
                <div key={w.id} className="wf-card disabled" title={w.disabledNote}>
                  <div className="wf-icon">{w.icon ?? "▢"}</div>
                  <div className="wf-name">{w.name}</div>
                  <div className="wf-desc">{w.tagline ?? w.description}</div>
                  <div className="wf-lock">🔒 {w.disabledNote ?? "Disabled"}</div>
                </div>
              ) : (
                <button key={w.id} className="wf-card" onClick={() => onSelect(w)}>
                  <div className="wf-icon">{w.icon ?? "▢"}</div>
                  <div className="wf-name">{w.name}</div>
                  <div className="wf-desc">{w.tagline ?? w.description}</div>
                </button>
              ),
            )}
          </div>
        </section>
      ))}
    </div>
  );
}
