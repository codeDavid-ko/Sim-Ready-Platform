"use client";

import type { WorkflowManifest } from "@/workflows/registry";

// SCR-02 — 매니페스트 목록을 카드 그리드로 렌더. 셸은 카드 내용을 모른다.
export function CardGrid({
  workflows,
  onSelect,
}: {
  workflows: WorkflowManifest[];
  onSelect: (m: WorkflowManifest) => void;
}) {
  if (workflows.length === 0) {
    return <p className="muted">등록된 워크플로우가 없습니다.</p>;
  }
  return (
    <div className="grid">
      {workflows.map((w) => (
        <button key={w.id} className="wf-card" onClick={() => onSelect(w)}>
          <div className="wf-icon">{w.icon ?? "▢"}</div>
          <div className="wf-name">{w.name}</div>
          <div className="wf-desc">{w.description}</div>
        </button>
      ))}
    </div>
  );
}
