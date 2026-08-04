"use client";

import { useState } from "react";
import { MODULES, type WorkflowManifest } from "@/workflows/registry";
import { WfIcon } from "./icons";

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
  // 카드 초기화: key 를 바꿔 모듈을 리마운트 → 입력/결과 등 내부 상태가 깨끗이 비워진다(각 카드 수정 불필요).
  const [resetKey, setResetKey] = useState(0);
  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 12 }}>
        <button className="ghost" onClick={onBack}>← 워크플로우 목록</button>
        <span className="row" style={{ gap: 10, alignItems: "center" }}>
          <button className="ghost" title="입력·결과를 비우고 처음부터 다시 작업" onClick={() => setResetKey((k) => k + 1)}>↻ 새로 작업</button>
          <b style={{ display: "inline-flex", alignItems: "center", gap: 8, color: "var(--vsc-fg-strong)" }}>
            <WfIcon id={manifest.id} size={19} strokeWidth={2} /> {manifest.name}
          </b>
        </span>
      </div>
      {Mod ? (
        <div className="wf-body">
          <Mod key={resetKey} manifest={manifest} onBack={onBack} />
        </div>
      ) : (
        <div className="card">
          <p className="err">이 워크플로우의 UI 모듈이 셸에 등록되어 있지 않습니다: {manifest.entry}</p>
        </div>
      )}
    </div>
  );
}
