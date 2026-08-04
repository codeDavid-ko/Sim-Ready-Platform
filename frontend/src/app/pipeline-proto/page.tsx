"use client";

/* 파이프라인 UI 정적 프로토타입 (감 잡기용 · 가짜 데이터 + 로컬 애니메이션, 백엔드 연결 없음).
 * 격리 페이지: /pipeline-proto. 기존 코드 안 건드림 → 롤백 = 이 파일 삭제.
 * 추천안 시연: 선형 레시피 빌더 + CI식 실행 스테이지. */

import { useEffect, useRef, useState } from "react";

const C = {
  bg: "#1e1e1e", card: "#252526", elev: "#2d2d30", border: "#3c3c3c",
  accent: "#0e639c", green: "#4ec9b0", amber: "#d7ba7d", red: "#f48771",
  fg: "#d4d4d4", muted: "#9d9d9d",
};

type Step = { n: number; icon: string; name: string; card: string; in: string; out: string };
const STEPS: Step[] = [
  { n: 1, icon: "🧩", name: "형상 정리·USD 변환", card: "asset-prep", in: "STEP/STL/glb", out: "USD" },
  { n: 2, icon: "🎨", name: "재질 추론", card: "material-usd", in: "USD", out: "재질 USD" },
  { n: 3, icon: "⚖️", name: "물성 추론", card: "mass-physics", in: "USD", out: "물성 USD" },
  { n: 4, icon: "🎬", name: "턴테이블 영상", card: "turntable", in: "USD", out: "mp4" },
];
const ART = ["STEP", "USD", "재질 USD", "물성 USD", "mp4"]; // 스텝 사이로 흐르는 산출물
// + 스텝 추가 후보(타입 호환 표시)
const ADD_CANDIDATES = [
  { name: "재질 추론 (material-usd)", ok: true }, { name: "물성 추론 (mass-physics)", ok: true },
  { name: "턴테이블 영상 (turntable)", ok: true }, { name: "관절 설정 (articulation)", ok: true },
  { name: "SD 텍스처 (sd-texture)", ok: false, why: "입력=프롬프트(USD 아님)" },
];

const PIPELINES = [
  { id: "gis", title: "GIS 변전소 sim-ready", steps: "STEP ▸ 정리 ▸ 재질 ▸ 물성 ▸ 영상", n: 4, last: "✓ 2분 전" },
  { id: "quick", title: "STEP→재질 빠른 변환", steps: "USD ▸ 재질 ▸ 물성", n: 3, last: "—" },
];

export default function PipelineProto() {
  const [tab, setTab] = useState<"cards" | "pipe">("pipe");
  const [view, setView] = useState<"list" | "build" | "run">("list");
  const [showAdd, setShowAdd] = useState(false);

  // 실행 애니메이션 상태
  const [running, setRunning] = useState(false);
  const [cur, setCur] = useState(0);          // 현재 진행 스텝 index
  const [prog, setProg] = useState(0);         // 현재 스텝 진행률 0~100
  const [done, setDone] = useState<number[]>([]);
  const [elapsed, setElapsed] = useState(0);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  function startRun() {
    setView("run"); setRunning(true); setCur(0); setProg(0); setDone([]); setElapsed(0);
  }
  useEffect(() => {
    if (!running) return;
    timer.current = setInterval(() => {
      setElapsed((e) => e + 0.1);
      setProg((p) => {
        const np = p + 7 + Math.round((cur % 3) * 1.5); // 스텝마다 살짝 다른 속도
        if (np >= 100) {
          setDone((d) => [...d, cur]);
          setCur((c) => {
            const nc = c + 1;
            if (nc >= STEPS.length) { setRunning(false); }
            return nc;
          });
          return 0;
        }
        return np;
      });
    }, 120);
    return () => { if (timer.current) clearInterval(timer.current); };
  }, [running, cur]);

  const allDone = done.length === STEPS.length;
  const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  const stageState = (i: number) => done.includes(i) ? "done" : (running && i === cur ? "run" : "wait");

  return (
    <div style={{ background: C.bg, color: C.fg, minHeight: "100vh", fontFamily: "system-ui, sans-serif" }}>
      <div className="wrap" style={{ padding: "20px 28px 60px" }}>
        {/* 헤더 + 탭 */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
          <h2 style={{ margin: 0, fontWeight: 700 }}>Sim-ready Platform <span style={{ color: C.muted, fontSize: 13, fontWeight: 400 }}>· 파이프라인 프로토타입</span></h2>
          <span style={{ color: C.muted, fontSize: 13 }}>code.david.ko · admin</span>
        </div>
        <div style={{ display: "inline-flex", border: `1px solid ${C.border}`, borderRadius: 6, overflow: "hidden", margin: "14px 0 22px" }}>
          {([["cards", "🧱 기능 카드"], ["pipe", "🔗 파이프라인"]] as const).map(([k, label]) => (
            <button key={k} onClick={() => { setTab(k); setView("list"); }}
              style={{ border: "none", borderRadius: 0, padding: "8px 18px", cursor: "pointer",
                background: tab === k ? C.accent : "transparent", color: tab === k ? "#fff" : C.muted }}>
              {label}
            </button>
          ))}
        </div>

        {tab === "cards" && (
          <div className="card" style={{ background: C.card, border: `1px solid ${C.border}` }}>
            <p style={{ color: C.muted }}>여기는 기존 기능 카드 그리드(데모에선 생략). 각 카드에 <b>“파이프라인에 추가 +”</b> 버튼이 붙어 블록을 레시피로 담는 흐름.</p>
          </div>
        )}

        {tab === "pipe" && view === "list" && (
          <>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
              <h3 style={{ margin: 0 }}>내 파이프라인</h3>
              <button onClick={() => setView("build")}>+ 새 파이프라인</button>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(320px,1fr))", gap: 14 }}>
              {PIPELINES.map((p) => (
                <div key={p.id} className="card" style={{ background: C.card, border: `1px solid ${C.border}`, margin: 0 }}>
                  <div style={{ fontWeight: 600, fontSize: 15 }}>{p.title}</div>
                  <div style={{ color: C.muted, fontSize: 13, margin: "6px 0" }}>{p.n}스텝 · {p.steps}</div>
                  <div style={{ color: C.muted, fontSize: 12, marginBottom: 10 }}>마지막 실행 {p.last}</div>
                  <div className="row">
                    <button onClick={startRun}>▶ 실행</button>
                    <button className="ghost" onClick={() => setView("build")}>✎ 편집</button>
                  </div>
                </div>
              ))}
            </div>
          </>
        )}

        {/* 빌더 — 선형 레시피 */}
        {tab === "pipe" && view === "build" && (
          <div className="card" style={{ background: C.card, border: `1px solid ${C.border}` }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <h3 style={{ margin: 0 }}>✎ “GIS 변전소 sim-ready”</h3>
              <div className="row">
                <button className="ghost" onClick={() => setView("list")}>← 목록</button>
                <button onClick={startRun}>▶ 실행</button>
              </div>
            </div>

            <div style={{ marginTop: 16, marginBottom: 6, color: C.muted, fontSize: 13 }}>📎 입력: STEP / USD 업로드</div>
            {STEPS.map((s, i) => (
              <div key={s.n}>
                <div style={{ textAlign: "center", color: C.green, fontSize: 12, lineHeight: 1.4 }}>
                  │ <span style={{ background: C.elev, padding: "1px 8px", borderRadius: 10, border: `1px solid ${C.border}` }}>{ART[i]}</span><br />▼
                </div>
                <div style={{ background: C.elev, border: `1px solid ${C.border}`, borderRadius: 8, padding: "12px 14px",
                  display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <div>
                    <div style={{ fontWeight: 600 }}><span style={{ color: C.muted, fontSize: 12 }}>스텝 {s.n}</span>　{s.icon} {s.name} <span style={{ color: C.muted, fontSize: 12 }}>({s.card})</span></div>
                    <div style={{ color: C.muted, fontSize: 12, marginTop: 3 }}>받음: {s.in}　→　냄: {s.out}</div>
                  </div>
                  <div className="row" style={{ flex: "none" }}>
                    <button className="ghost" style={{ padding: "2px 10px" }}>⚙ 옵션</button>
                    <button className="ghost" style={{ padding: "2px 10px" }}>✕</button>
                  </div>
                </div>
              </div>
            ))}

            <div style={{ textAlign: "center", marginTop: 14 }}>
              <button className="ghost" onClick={() => setShowAdd((v) => !v)}>+ 스텝 추가</button>
            </div>
            {showAdd && (
              <div style={{ background: C.elev, border: `1px solid ${C.border}`, borderRadius: 8, padding: 10, marginTop: 8, maxWidth: 420, marginInline: "auto" }}>
                <div style={{ color: C.muted, fontSize: 12, marginBottom: 6 }}>현재 출력 <b>mp4</b> 기준 — 호환되는 카드만 활성:</div>
                {ADD_CANDIDATES.map((c) => (
                  <div key={c.name} style={{ padding: "5px 8px", borderRadius: 5, fontSize: 13, opacity: c.ok ? 1 : 0.4,
                    cursor: c.ok ? "pointer" : "not-allowed", display: "flex", justifyContent: "space-between" }}>
                    <span>{c.ok ? "✅" : "🚫"} {c.name}</span>
                    {!c.ok && <span style={{ color: C.muted, fontSize: 11 }}>{c.why}</span>}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* 실행 — CI식 스테이지 */}
        {tab === "pipe" && view === "run" && (
          <div className="card" style={{ background: C.card, border: `1px solid ${C.border}` }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <h3 style={{ margin: 0 }}>▶ “GIS 변전소 sim-ready” {allDone ? <span style={{ color: C.green }}>· 완료 ✓</span> : <span style={{ color: C.amber }}>· 실행 중</span>} <span style={{ color: C.muted, fontSize: 13, fontWeight: 400 }}>· 경과 {mmss(elapsed)}</span></h3>
              <div className="row">
                {!allDone && <button className="ghost" onClick={() => { setRunning(false); }}>취소</button>}
                <button className="ghost" onClick={() => setView("list")}>← 목록</button>
                {allDone && <button onClick={startRun}>다시 실행</button>}
              </div>
            </div>

            {/* 스테이지 바 */}
            <div style={{ display: "flex", alignItems: "flex-start", gap: 0, marginTop: 22, overflowX: "auto", paddingBottom: 8 }}>
              {STEPS.map((s, i) => {
                const st = stageState(i);
                const col = st === "done" ? C.green : st === "run" ? C.amber : C.muted;
                const icon = st === "done" ? "✓" : st === "run" ? "⏳" : "⏸";
                return (
                  <div key={s.n} style={{ display: "flex", alignItems: "flex-start" }}>
                    <div style={{ width: 150 }}>
                      <div style={{ border: `1.5px solid ${col}`, borderRadius: 8, padding: "10px 8px", textAlign: "center", background: st === "run" ? "rgba(215,186,125,.08)" : "transparent" }}>
                        <div style={{ fontSize: 13, fontWeight: 600 }}>{s.icon} {s.name}</div>
                        <div style={{ color: col, fontSize: 12, marginTop: 4 }}>
                          {icon} {st === "done" ? `${8 + i * 11}s` : st === "run" ? `${prog}%` : "대기"}
                        </div>
                        {st === "run" && (
                          <div style={{ height: 4, background: C.border, borderRadius: 3, marginTop: 6, overflow: "hidden" }}>
                            <div style={{ width: `${prog}%`, height: "100%", background: C.amber }} />
                          </div>
                        )}
                      </div>
                      {/* 산출물 칩 */}
                      <div style={{ textAlign: "center", marginTop: 6, minHeight: 26 }}>
                        {st === "done" && (
                          <>
                            <span style={{ fontSize: 11, background: C.elev, border: `1px solid ${C.border}`, borderRadius: 10, padding: "2px 8px", cursor: "pointer" }}>⬇ {s.out}</span>
                            {s.card === "material-usd" && <span style={{ fontSize: 11, marginLeft: 4, color: C.muted }}>🖼</span>}
                          </>
                        )}
                      </div>
                    </div>
                    {i < STEPS.length - 1 && (
                      <div style={{ width: 30, textAlign: "center", color: done.includes(i) ? C.green : C.border, paddingTop: 22, fontSize: 18 }}>▶</div>
                    )}
                  </div>
                );
              })}
            </div>

            <div style={{ marginTop: 10, paddingTop: 10, borderTop: `1px solid ${C.border}`, color: C.muted, fontSize: 13 }}>
              {allDone
                ? "✓ 전 스텝 완료 — 최종 산출물(mp4) + 중간 USD 전부 받을 수 있음."
                : running
                  ? `현재 ${STEPS[Math.min(cur, 3)].n}번 “${STEPS[Math.min(cur, 3)].name}” 진행 중…`
                  : "⏹ 취소됨 — 완료된 스텝까지 산출물은 받을 수 있고, 그 스텝부터 재실행 가능."}
            </div>
          </div>
        )}

        <p style={{ color: C.muted, fontSize: 12, marginTop: 24 }}>
          ⓘ 정적 프로토타입(가짜 데이터·로컬 애니메이션). 실제 실행/저장 없음. 롤백 = 이 페이지 파일만 삭제.
        </p>
      </div>
    </div>
  );
}
