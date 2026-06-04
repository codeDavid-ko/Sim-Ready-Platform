"use client";

import { MODULES, type WorkflowManifest } from "@/workflows/registry";

// SCR-03 — 선택된 워크플로우 모듈을 화면 영역에 마운트하는 프레임.
// 셸은 manifest.entry 로 모듈을 찾아 띄울 뿐, 내부 UI/로직은 모른다.
export function WorkflowContainer({
  manifest,
  onBack,
}: {
  manifest: WorkflowManifest;
  onBack: () => void;
}) {
  const Mod = MODULES[manifest.entry];
  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 12 }}>
        <button className="ghost" onClick={onBack}>← 워크플로우 목록</button>
        <b>{manifest.icon} {manifest.name}</b>
      </div>
      {Mod ? (
        <Mod manifest={manifest} onBack={onBack} />
      ) : (
        <div className="card">
          <p className="err">이 워크플로우의 UI 모듈이 셸에 등록되어 있지 않습니다: {manifest.entry}</p>
        </div>
      )}
    </div>
  );
}
