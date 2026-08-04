"use client";

import { useEffect, useRef, useState } from "react";

type Prog = { phase?: string; frame?: number; total?: number } | null;

const PHASE_KO: Record<string, string> = {
  opening_stage: "USD 여는 중", stage_opened: "USD 연 후 준비 중", cleaning: "입력 정리 중",
  collect_joints: "조인트 수집 중", strip_physics: "장면 준비 중", authoring_animation: "장면 준비 중",
  render_frames: "렌더 중",
};

/** 실행 중 진행 표시. 백엔드 실제 진행률(progress: 프레임 n/total)이 오면 그걸로 정확한 바·남은시간을,
 *  없으면 etaSec 추정 바, 그것도 없으면 무한 애니메이션. + 취소 버튼. */
export default function JobProgress({
  busy,
  onCancel,
  etaSec,
  hint,
  progress,
}: {
  busy: boolean;
  onCancel?: () => void;
  etaSec?: number;
  hint?: string;
  progress?: Prog;
}) {
  const [elapsed, setElapsed] = useState(0);
  const start = useRef<number>(0);

  useEffect(() => {
    if (!busy) return;
    start.current = Date.now();
    setElapsed(0);
    const id = setInterval(() => setElapsed(Math.floor((Date.now() - start.current) / 1000)), 250);
    return () => clearInterval(id);
  }, [busy]);

  if (!busy) return null;

  const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.max(0, Math.round(s)) % 60).padStart(2, "0")}`;

  // 1) 실제 진행률(프레임 기반) — 가장 정확
  const total = progress?.total ?? 0;
  const frame = progress?.frame ?? 0;
  const ph = progress?.phase ?? "";
  let frac: number | null = null;
  let label = "";
  if (total > 0 && ph) {
    if (ph === "render_frames" && frame < total) {
      frac = frame / total;
      const remain = frame > 0 ? (elapsed / frame) * (total - frame) : null; // 실측 프레임속도로 ETA
      label = `렌더 중 ${frame}/${total} 프레임${remain != null ? ` · 약 ${mmss(remain)} 남음` : ""}`;
    } else if (ph === "render_frames" && frame >= total) {
      frac = 0.99;
      label = "영상 인코딩(ffmpeg) 중…";
    } else {
      // 렌더 전 단계(여는 중/준비 중). 너무 오래 머물면 원인 추정을 보여준다.
      frac = 0.04;
      const slow = elapsed > 90;
      const why = (ph === "opening_stage" || ph === "stage_opened")
        ? " ⚠ 예상보다 오래 — 원격(온라인) 참조나 대용량 USD 때문일 수 있어요. 자기완결 USD 권장"
        : " ⚠ 예상보다 오래 걸리는 중";
      label = `${PHASE_KO[ph] ?? ph}…${slow ? why : ""}`;
    }
  } else if (etaSec) {
    // 2) 추정 바(백엔드 진행률 아직 없을 때)
    frac = Math.min(elapsed / etaSec, 0.97);
    label = elapsed < etaSec ? `약 ${mmss(etaSec - elapsed)} 남음(예상)` : "마무리 중…";
  }

  return (
    <div style={{ marginTop: 10 }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
        <span className="muted" style={{ fontSize: 12 }}>
          {mmss(elapsed)} 경과{label ? ` · ${label}` : ""}{hint ? ` · ${hint}` : ""}
        </span>
        {onCancel && (
          <button type="button" className="ghost" style={{ padding: "2px 10px", fontSize: 12 }} onClick={onCancel}>
            취소
          </button>
        )}
      </div>
      <div className={`jp-track${frac === null ? " jp-indet" : ""}`}>
        <div className="jp-bar" style={frac === null ? undefined : { width: `${(frac * 100).toFixed(1)}%` }} />
      </div>
    </div>
  );
}
