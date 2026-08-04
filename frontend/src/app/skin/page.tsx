"use client";

/* UI 스킨 데모 (보기 전용) — Wise 풍 라이트 테마에 **실제 우리 데이터**(워크플로우/카테고리/최근 실행)를
 * 입힌 미리보기. 기존 코드 0 변경. 롤백 = 이 파일 삭제. /skin 접속. 클릭 동작은 데모 수준. */

import { useEffect, useState } from "react";
import { API_BASE } from "@/lib/api";
import { authHeaders } from "@/lib/auth";

const C = {
  page: "#f4f5f7", panel: "#ffffff", soft: "#f4f5f7", border: "#ececef",
  ink: "#0e1b13", muted: "#6b7280", green: "#9fe870", greenInk: "#163300", greenSoft: "#e8f8d8",
};

type WF = { id: string; name: string; icon?: string; tagline?: string; description?: string; category?: string; hidden?: boolean; disabled?: boolean };
type Asset = { id: string; workflow_id?: string; name?: string; filename?: string; bytes?: number };

// 실제 카테고리 키 → 표시 라벨/순서 (CardGrid 와 동일)
const CATS: { key: string; label: string }[] = [
  { key: "ndotlight-trinix", label: "NdotLight Trinix" },
  { key: "nvidia-content-agents", label: "NVIDIA Content Agents" },
  { key: "comparison", label: "비교 (Comparison)" },
  { key: "etc", label: "기타 (유틸리티)" },
];

export default function SkinDemo() {
  const [wfs, setWfs] = useState<WF[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [nav, setNav] = useState("홈");

  useEffect(() => {
    (async () => {
      try {
        const r = await fetch(`${API_BASE}/api/workflows`, { headers: authHeaders(), cache: "no-store" });
        setWfs((await r.json()).workflows ?? []);
      } catch { /* */ }
      try {
        const r = await fetch(`${API_BASE}/api/workflows/assets/recent?limit=8`, { headers: authHeaders(), cache: "no-store" });
        setAssets((await r.json()).assets ?? []);
      } catch { /* */ }
    })();
  }, []);

  const visible = wfs.filter((w) => !w.hidden);
  const iconOf = (id?: string) => wfs.find((w) => w.id === id)?.icon ?? "📄";
  const catCount = (key: string) => visible.filter((w) => w.category === key).length;
  const groups = CATS.map((c) => ({ ...c, items: visible.filter((w) => w.category === c.key) })).filter((g) => g.items.length);

  const NAV = [
    { icon: "🏠", label: "홈" }, { icon: "🧩", label: "워크플로우" }, { icon: "🔗", label: "파이프라인" },
    { icon: "🗂", label: "자산" }, { icon: "👤", label: "사용자" },
  ];

  return (
    <div style={{ background: C.page, minHeight: "100vh", padding: 20, fontFamily: "Inter, system-ui, sans-serif", color: C.ink }}>
      {/* 상단 바 */}
      <div style={{ display: "flex", alignItems: "center", gap: 20, maxWidth: 1280, margin: "0 auto 18px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, fontWeight: 800, fontSize: 18 }}>
          <span style={{ width: 30, height: 30, borderRadius: 9, background: C.greenInk, color: C.green, display: "grid", placeItems: "center", fontSize: 16 }}>S</span>
          Sim-ready
        </div>
        <div style={{ flex: 1, display: "flex", alignItems: "center", gap: 8, background: C.panel, border: `1px solid ${C.border}`, borderRadius: 999, padding: "10px 16px", color: C.muted, maxWidth: 560 }}>
          <span>🔍</span><span style={{ fontSize: 14 }}>검색 — 워크플로우 · 자산 · 파이프라인</span>
          <span style={{ marginLeft: "auto", fontSize: 12, background: C.soft, borderRadius: 6, padding: "2px 7px" }}>⌘K</span>
        </div>
        <div style={{ position: "relative", width: 40, height: 40, borderRadius: 999, background: C.panel, border: `1px solid ${C.border}`, display: "grid", placeItems: "center" }}>🔔
          <span style={{ position: "absolute", top: 9, right: 10, width: 7, height: 7, borderRadius: 999, background: "#ef4444" }} /></div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ width: 38, height: 38, borderRadius: 999, background: C.greenSoft, color: C.greenInk, display: "grid", placeItems: "center", fontWeight: 700, fontSize: 13 }}>DK</span>
          <span style={{ fontWeight: 600, fontSize: 14 }}>code.david.ko ⌄</span>
        </div>
      </div>

      {/* 메인 패널 */}
      <div style={{ maxWidth: 1280, margin: "0 auto", background: C.panel, borderRadius: 24, border: `1px solid ${C.border}`, display: "grid", gridTemplateColumns: "230px 1fr", overflow: "hidden", boxShadow: "0 1px 3px rgba(0,0,0,.04)" }}>
        {/* 사이드바 */}
        <div style={{ padding: "28px 16px", borderRight: `1px solid ${C.border}` }}>
          {NAV.map((n) => (
            <div key={n.label} onClick={() => setNav(n.label)}
              style={{ display: "flex", alignItems: "center", gap: 12, padding: "11px 14px", borderRadius: 12, marginBottom: 4, cursor: "pointer", fontWeight: 600, fontSize: 15,
                background: nav === n.label ? C.soft : "transparent", color: nav === n.label ? C.ink : C.muted }}>
              <span style={{ fontSize: 18 }}>{n.icon}</span>{n.label}
            </div>
          ))}
          <div style={{ marginTop: 24, padding: "12px 14px", borderRadius: 12, background: C.greenSoft, color: C.greenInk, fontSize: 13, fontWeight: 600 }}>🔗 새 파이프라인 만들기</div>
        </div>

        {/* 콘텐츠 */}
        <div style={{ padding: "34px 40px 48px" }}>
          <div style={{ color: C.muted, fontSize: 14 }}>활성 워크플로우</div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 4 }}>
            <div style={{ fontSize: 40, fontWeight: 800, letterSpacing: -1 }}>{visible.length} <span style={{ fontSize: 22, fontWeight: 700, color: C.muted }}>개</span></div>
            <span style={{ fontSize: 18 }}>🧩</span>
          </div>
          <div style={{ display: "flex", gap: 10, marginTop: 18 }}>
            {["↑ 새 실행", "＋ 자산 업로드", "🔗 파이프라인"].map((t, i) => (
              <span key={t} style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "9px 16px", borderRadius: 999, fontWeight: 600, fontSize: 14, cursor: "pointer",
                background: i === 0 ? C.green : C.soft, color: i === 0 ? C.greenInk : C.ink }}>{t}</span>
            ))}
          </div>

          {/* 카테고리 카드 행 — 실제 카테고리별 개수 */}
          <div style={{ display: "grid", gridTemplateColumns: `repeat(${Math.max(1, CATS.length)}, 1fr)`, gap: 14, marginTop: 26 }}>
            {CATS.map((c) => (
              <div key={c.key} style={{ background: C.soft, borderRadius: 16, padding: "18px", cursor: "pointer" }}>
                <div style={{ fontSize: 24 }}>{c.key === "nvidia-content-agents" ? "🟩" : c.key === "comparison" ? "⚖️" : c.key === "etc" ? "🛠" : "🎨"}</div>
                <div style={{ fontWeight: 700, fontSize: 14, marginTop: 18 }}>{c.label}</div>
                <div style={{ color: C.muted, fontSize: 12.5, marginTop: 2 }}>{catCount(c.key)}개 워크플로우</div>
              </div>
            ))}
          </div>

          {/* 실제 워크플로우 카드 (카테고리별) */}
          {groups.map((g) => (
            <div key={g.key} style={{ marginTop: 30 }}>
              <div style={{ fontWeight: 700, fontSize: 16, marginBottom: 10 }}>{g.label}</div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 12 }}>
                {g.items.map((w) => (
                  <div key={w.id} style={{ background: C.soft, borderRadius: 14, padding: "16px", opacity: w.disabled ? 0.5 : 1, cursor: "pointer" }}>
                    <div style={{ fontSize: 22 }}>{w.icon ?? "▢"}</div>
                    <div style={{ fontWeight: 600, fontSize: 14, marginTop: 12 }}>{w.name}</div>
                    <div style={{ color: C.muted, fontSize: 12, marginTop: 3, lineHeight: 1.4 }}>{w.tagline ?? w.description}</div>
                  </div>
                ))}
              </div>
            </div>
          ))}

          {/* 최근 실행 — 실제 자산 */}
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 32 }}>
            <div style={{ fontWeight: 700, fontSize: 18 }}>최근 실행</div>
            <span style={{ fontSize: 13, color: C.greenInk, fontWeight: 600, textDecoration: "underline", cursor: "pointer" }}>전체 보기</span>
          </div>
          <div style={{ marginTop: 8 }}>
            {assets.length === 0 && <p style={{ color: C.muted, fontSize: 13 }}>아직 실행/산출물이 없어요.</p>}
            {assets.map((a, i) => (
              <div key={a.id} style={{ display: "flex", alignItems: "center", gap: 14, padding: "13px 4px", borderTop: i ? `1px solid ${C.border}` : "none" }}>
                <span style={{ width: 42, height: 42, borderRadius: 999, background: C.soft, display: "grid", placeItems: "center", fontSize: 18 }}>{iconOf(a.workflow_id)}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 600, fontSize: 14.5, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{a.name || a.filename}</div>
                  <div style={{ color: C.muted, fontSize: 12.5 }}>{a.workflow_id} · {a.filename}</div>
                </div>
                <div style={{ fontWeight: 700, fontSize: 13, color: C.muted }}>{a.bytes ? `${(a.bytes / 1024).toFixed(0)} KB` : ""}</div>
              </div>
            ))}
          </div>

          {/* 프로모 배너 */}
          <div style={{ marginTop: 30, borderRadius: 20, overflow: "hidden", position: "relative",
            background: `linear-gradient(120deg, ${C.greenInk}, #2e6b2e 60%, ${C.green})`, color: "#fff", padding: "30px 32px", minHeight: 150 }}>
            <div style={{ fontSize: 13, opacity: .85 }}>Sim-ready 파이프라인</div>
            <div style={{ fontSize: 30, fontWeight: 800, marginTop: 8, letterSpacing: -0.5 }}>BUILD ONCE,<br />RUN ANYWHERE.</div>
            <div style={{ position: "absolute", right: 28, bottom: 22, width: 150, height: 92, borderRadius: 12, background: "rgba(159,232,112,.5)", border: "1px solid rgba(255,255,255,.4)" }} />
          </div>
        </div>
      </div>

      <p style={{ maxWidth: 1280, margin: "14px auto 0", color: C.muted, fontSize: 12 }}>
        ⓘ UI 스킨 데모(보기 전용). 콘텐츠는 실제 데이터(워크플로우·최근 산출물). 적용은 별도, 롤백 = `app/skin/page.tsx` 삭제.
      </p>
    </div>
  );
}
