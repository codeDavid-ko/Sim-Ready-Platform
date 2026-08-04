"use client";

import { useEffect, useRef, useState } from "react";
import { apiJson } from "@/lib/api";
import { canUseCard, clearToken, getToken, getUser, setToken, setUser, type SessionUser } from "@/lib/auth";
import { CardGrid } from "@/components/CardGrid";
import { WorkflowContainer } from "@/components/WorkflowContainer";
import { UserAdmin } from "@/components/UserAdmin";
import Pipelines from "@/workflows/pipelines/Pipelines";
import { isDevCard, type WorkflowManifest } from "@/workflows/registry";
import { House, Puzzle, Workflow, FlaskConical, Users, Bell, Activity, Search, X } from "lucide-react";

const PIPE_M = { id: "pipelines", name: "파이프라인", description: "", version: "1.0.0", entry: "pipelines" } as WorkflowManifest;

type Status = { auth_required: boolean; needs_setup: boolean };
type LoginResp = { token: string; username: string; role: string; cards?: string[] };

// 셸(Shell) — 인증 / 레지스트리(카드 그리드) / 마운트(컨테이너) + 관리자 사용자관리.
export default function Home() {
  const [authed, setAuthed] = useState(false);
  const [needsSetup, setNeedsSetup] = useState(false);
  const [user, setUserState] = useState<SessionUser | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [error, setError] = useState<string | null>(null);

  const [workflows, setWorkflows] = useState<WorkflowManifest[]>([]);
  const [selected, setSelected] = useState<WorkflowManifest | null>(null);
  const [denied, setDenied] = useState<string | null>(null);   // 권한 없는 카드 클릭 시 안내
  const [showAdmin, setShowAdmin] = useState(false);
  const [section, setSection] = useState<"home" | "cards" | "pipelines" | "labs">("home");  // 홈/기능카드/파이프라인/Labs
  const [pipeCount, setPipeCount] = useState(0);
  const [pipeDefs, setPipeDefs] = useState<{ id: string; title: string }[]>([]);
  const [activeUsers, setActiveUsers] = useState(0);
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);

  // 인증 상태 확인 (SCR-01)
  useEffect(() => {
    apiJson<Status>("/api/status")
      .then((s) => setNeedsSetup(s.needs_setup))
      .catch(() => {});
    if (getToken()) {
      setAuthed(true);
      setUserState(getUser());
    }
  }, []);

  // 레지스트리 로드 (SCR-02)
  useEffect(() => {
    if (!authed) return;
    apiJson<{ workflows: WorkflowManifest[] }>("/api/workflows")
      .then((r) => {
        setWorkflows(r.workflows);
        // 새로고침 시 마지막 위치 복원(카드/섹션/관리자) — 카드에 머물게.
        try {
          const nav = JSON.parse(localStorage.getItem("sr_nav") || "{}");
          if (nav.card) {
            const w = r.workflows.find((x) => x.id === nav.card);
            if (w) { setSelected(w); return; }
          }
          if (nav.section === "cards" || nav.section === "pipelines" || nav.section === "labs") setSection(nav.section);
        } catch { /* */ }
      })
      .catch(() => {});
    apiJson<{ pipelines: { id: string; title: string }[] }>("/api/workflows/pipelines/defs")
      .then((r) => { const ps = r.pipelines ?? []; setPipeCount(ps.length); setPipeDefs(ps.map((p) => ({ id: p.id, title: p.title }))); })
      .catch(() => {});
    apiJson<{ count: number }>("/api/active-users")
      .then((r) => setActiveUsers(r.count)).catch(() => {});
    // /me 로 항상 보강 — 권한(cards) 변경이 새로고침 시 즉시 반영되도록.
    apiJson<{ username: string; role: string; cards?: string[] }>("/api/me")
      .then((m) => { setUser(m); setUserState(m); })
      .catch(() => {});
  }, [authed]);

  // 현재 위치(카드/섹션/관리자)를 저장 — 새로고침해도 그 자리에 머물게.
  useEffect(() => {
    if (!authed) return;
    try {
      localStorage.setItem("sr_nav", JSON.stringify({
        card: selected?.id ?? null, section, admin: showAdmin,
      }));
    } catch { /* */ }
  }, [authed, selected, section, showAdmin]);

  // ⌘K / Ctrl+K 로 검색 포커스, Esc 로 검색 비우기
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        searchRef.current?.focus();
        searchRef.current?.select();
      } else if (e.key === "Escape" && document.activeElement === searchRef.current) {
        setQuery("");
        searchRef.current?.blur();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function applyLogin(r: LoginResp) {
    setToken(r.token);
    const u = { username: r.username, role: r.role, cards: r.cards ?? [] };
    setUser(u);
    setUserState(u);
    setAuthed(true);
    setNeedsSetup(false);
  }

  async function login(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const r = await apiJson<LoginResp>("/api/login", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      applyLogin(r);
    } catch {
      setError("아이디 또는 비밀번호가 올바르지 않습니다.");
    }
  }

  async function setup(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!username.trim() || !password) { setError("아이디와 비밀번호를 입력하세요."); return; }
    if (password !== password2) { setError("비밀번호 확인이 일치하지 않습니다."); return; }
    try {
      const r = await apiJson<LoginResp>("/api/setup", {
        method: "POST",
        body: JSON.stringify({ username: username.trim(), password }),
      });
      applyLogin(r);
    } catch (err) {
      setError(String((err as Error).message).includes("400") ? "이미 초기화되었습니다. 새로고침 후 로그인하세요." : "관리자 생성에 실패했습니다.");
    }
  }

  function logout() {
    clearToken();
    setAuthed(false);
    setUserState(null);
    setSelected(null);
    setShowAdmin(false);
    setUsername("");
    setPassword("");
  }

  // SCR-00 최초 관리자 설정 (사용자가 0명일 때)
  if (!authed && needsSetup) {
    return (
      <main className="authwrap">
        <div className="card">
          <h2>Sim-ready Platform — 초기 설정</h2>
          <p className="muted">최초 실행입니다. 사용할 <b>관리자 아이디와 비밀번호</b>를 직접 정하세요.</p>
          <form onSubmit={setup}>
            <label>관리자 아이디</label>
            <input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus autoComplete="username" placeholder="예: admin" />
            <label>비밀번호</label>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />
            <label>비밀번호 확인</label>
            <input type="password" value={password2} onChange={(e) => setPassword2(e.target.value)} autoComplete="new-password" />
            {error && <p className="err">{error}</p>}
            <div style={{ marginTop: 12 }}><button type="submit">관리자 생성 후 시작</button></div>
          </form>
        </div>
      </main>
    );
  }

  // SCR-01 로그인
  if (!authed) {
    return (
      <main className="authwrap">
        <div className="card">
          <h2>Sim-ready Platform</h2>
          <form onSubmit={login}>
            <label>아이디</label>
            <input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus autoComplete="username" />
            <label>비밀번호</label>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
            {error && <p className="err">{error}</p>}
            <div style={{ marginTop: 12 }}><button type="submit">로그인</button></div>
          </form>
        </div>
      </main>
    );
  }

  const initials = (user?.username ?? "U").replace(/[^A-Za-z0-9]/g, "").slice(0, 2).toUpperCase() || "U";
  const cardCount = workflows.filter((w) => !w.hidden && !isDevCard(w)).length;  // 기능 카드(정식)
  const devCards = workflows.filter((w) => !w.hidden && isDevCard(w));           // Labs(개발 중)
  const onHome = !selected && !showAdmin && section === "home";
  const onCards = !selected && !showAdmin && section === "cards";
  const onPipe = !selected && !showAdmin && section === "pipelines";
  const onLabs = !selected && !showAdmin && section === "labs";
  const goHome = () => { setSelected(null); setShowAdmin(false); setSection("home"); };
  const goCards = () => { setSelected(null); setShowAdmin(false); setSection("cards"); };
  const goPipe = () => { setSelected(null); setShowAdmin(false); setSection("pipelines"); };
  const goLabs = () => { setSelected(null); setShowAdmin(false); setSection("labs"); };

  // 검색 — 카드(이름·태그라인·설명·카테고리·id) + 저장된 파이프라인(제목)
  const q = query.trim().toLowerCase();
  const searching = q.length > 0;
  const matchCards = !searching ? [] : workflows.filter((w) =>
    !w.hidden && [w.name, w.tagline, w.description, w.category, w.id].some((s) => (s ?? "").toLowerCase().includes(q)),
  );
  const matchPipes = !searching ? [] : pipeDefs.filter((p) => (p.title ?? "").toLowerCase().includes(q));
  // 권한 게이트: 허용 카드면 열고, 아니면 "권한 없음" 안내(프론트 표시용).
  const tryOpen = (w: WorkflowManifest) => {
    if (!canUseCard(user, w.id)) { setDenied(w.name); return; }
    setDenied(null); setQuery(""); setShowAdmin(false); setSelected(w);
  };
  const openCard = tryOpen;
  const cardCanUse = (w: WorkflowManifest) => canUseCard(user, w.id);

  const navStyle = (active: boolean): React.CSSProperties => ({
    display: "flex", alignItems: "center", gap: 12, padding: "11px 14px", borderRadius: 12, marginBottom: 4,
    cursor: "pointer", fontWeight: 600, fontSize: 15, textDecoration: "none",
    color: active ? "var(--vsc-fg-strong)" : "var(--vsc-muted)", background: active ? "var(--vsc-elev)" : "transparent",
  });

  return (
    <main className="wrap" style={{ maxWidth: 1320 }}>
      {/* 상단 바 */}
      <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 18 }}>
        <div onClick={() => { setQuery(""); goHome(); }} role="button" tabIndex={0}
          onKeyDown={(e) => { if (e.key === "Enter") { setQuery(""); goHome(); } }}
          title="홈으로"
          style={{ display: "flex", alignItems: "center", gap: 10, fontWeight: 800, fontSize: 18, color: "var(--vsc-fg-strong)", cursor: "pointer" }}>
          <span style={{ width: 30, height: 30, borderRadius: 9, background: "var(--vsc-accent-ink)", color: "var(--vsc-accent)", display: "grid", placeItems: "center", fontSize: 16 }}>S</span>
          Sim-ready
        </div>
        <div style={{ flex: 1, display: "flex", alignItems: "center", gap: 8, background: "var(--vsc-panel)", border: "1px solid var(--vsc-border)", borderRadius: 999, padding: "10px 16px", color: "var(--vsc-muted)", maxWidth: 520 }}>
          <Search size={16} strokeWidth={2} />
          <input
            ref={searchRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="검색 — 카드 · 파이프라인"
            style={{ flex: 1, border: "none", outline: "none", background: "transparent", fontSize: 14, color: "var(--vsc-fg-strong)" }}
          />
          {query
            ? <span onClick={() => { setQuery(""); searchRef.current?.focus(); }} style={{ cursor: "pointer", display: "inline-flex" }} title="지우기"><X size={15} strokeWidth={2} /></span>
            : <span style={{ fontSize: 12, background: "var(--vsc-elev)", borderRadius: 6, padding: "2px 7px" }}>⌘K</span>}
        </div>
        <div style={{ width: 40, height: 40, borderRadius: 999, background: "var(--vsc-panel)", border: "1px solid var(--vsc-border)", display: "grid", placeItems: "center", color: "var(--vsc-muted)" }}><Bell size={18} strokeWidth={1.9} /></div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ width: 38, height: 38, borderRadius: 999, background: "var(--vsc-accent)", color: "var(--vsc-accent-ink)", display: "grid", placeItems: "center", fontWeight: 700, fontSize: 13 }}>{initials}</span>
          {user && <span style={{ fontWeight: 600, fontSize: 14, color: "var(--vsc-fg-strong)" }}>{user.username}</span>}
        </div>
      </div>

      {/* 패널: 사이드바 | 콘텐츠 */}
      <div style={{ background: "var(--vsc-panel)", border: "1px solid var(--vsc-border)", borderRadius: 24, display: "grid", gridTemplateColumns: "230px 1fr", overflow: "hidden", boxShadow: "0 1px 2px rgba(16,27,19,.04)" }}>
        <nav style={{ padding: "24px 16px", borderRight: "1px solid var(--vsc-border)" }}>
          <div style={navStyle(onHome)} onClick={goHome}><House size={18} strokeWidth={2} />홈</div>
          <div style={navStyle(onCards)} onClick={goCards}><Puzzle size={18} strokeWidth={2} />기능 카드</div>
          <div style={navStyle(onPipe)} onClick={goPipe}><Workflow size={18} strokeWidth={2} />파이프라인</div>
          <div style={navStyle(onLabs)} onClick={goLabs}><FlaskConical size={18} strokeWidth={2} />Labs <span style={{ fontSize: 10, fontWeight: 700, color: "var(--vsc-accent-ink)", background: "var(--vsc-accent)", borderRadius: 999, padding: "1px 6px", marginLeft: "auto" }}>개발중</span></div>
          {user?.role === "admin" && (
            <div style={navStyle(showAdmin)} onClick={() => { setSelected(null); setShowAdmin(true); }}><Users size={18} strokeWidth={2} />사용자 관리</div>
          )}
          <div style={{ marginTop: 24 }}><button className="ghost" onClick={logout} style={{ width: "100%" }}>로그아웃</button></div>
        </nav>

        <div style={{ padding: "28px 32px 40px", minWidth: 0 }}>
          {denied && (
            <div style={{ marginBottom: 16, padding: "10px 14px", borderRadius: 10, background: "rgba(176,0,32,.10)",
              border: "1px solid rgba(176,0,32,.3)", display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ color: "#b00020", fontWeight: 600 }}>🔒 “{user?.username}”는 “{denied}” 카드 접근 권한이 없습니다.</span>
              <span className="muted" style={{ fontSize: 12 }}>관리자에게 권한을 요청하세요.</span>
              <button className="ghost" style={{ marginLeft: "auto", padding: "2px 10px", fontSize: 12 }} onClick={() => setDenied(null)}>닫기</button>
            </div>
          )}
          {searching ? (
            <>
              <h2 style={{ marginTop: 0, display: "flex", alignItems: "center", gap: 9 }}><Search size={20} strokeWidth={2} /> 검색 결과</h2>
              <p className="muted" style={{ marginTop: 2 }}>“{query}” — 카드 {matchCards.length}개 · 파이프라인 {matchPipes.length}개</p>
              {matchPipes.length > 0 && (
                <div style={{ marginTop: 16 }}>
                  <h3 className="cat-title">파이프라인</h3>
                  <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 8 }}>
                    {matchPipes.map((p) => (
                      <div key={p.id} className="wf-card" onClick={goPipe} style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: 12, padding: "12px 16px" }}>
                        <Workflow size={18} strokeWidth={1.9} /><b style={{ color: "var(--vsc-fg-strong)" }}>{p.title}</b>
                        <span className="muted" style={{ marginLeft: "auto", fontSize: 12 }}>파이프라인 열기 →</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {matchCards.length > 0 && (
                <div style={{ marginTop: 18 }}><CardGrid workflows={matchCards} onSelect={tryOpen} canUse={cardCanUse} /></div>
              )}
              {matchCards.length === 0 && matchPipes.length === 0 && (
                <p className="muted" style={{ marginTop: 18 }}>일치하는 카드나 파이프라인이 없어요. 다른 키워드로 검색해 보세요.</p>
              )}
            </>
          ) : showAdmin ? (
            <UserAdmin onBack={() => setShowAdmin(false)} />
          ) : selected ? (
            <WorkflowContainer manifest={selected} onBack={() => setSelected(null)} />
          ) : section === "pipelines" ? (
            <Pipelines manifest={PIPE_M} onBack={goHome} />
          ) : section === "labs" ? (
            <>
              <h2 style={{ marginTop: 0, display: "flex", alignItems: "center", gap: 9 }}><FlaskConical size={22} strokeWidth={2} /> Labs <span style={{ fontSize: 14, fontWeight: 500, color: "var(--vsc-muted)" }}>· 실험·개발 중인 기능</span></h2>
              <p className="muted" style={{ marginTop: 2 }}>아직 다듬는 중인 기능이에요 — 동작이 불안정하거나 잠겨 있을 수 있어요.</p>
              {devCards.length === 0
                ? <p className="muted" style={{ marginTop: 18 }}>지금은 실험 중인 기능이 없어요.</p>
                : <div style={{ marginTop: 18 }}><CardGrid workflows={devCards} onSelect={tryOpen} canUse={cardCanUse} /></div>}
            </>
          ) : section === "cards" ? (
            <>
              <h2 style={{ marginTop: 0 }}>기능 카드</h2>
              <p className="muted" style={{ marginTop: 2 }}>카드를 골라 실행하세요. 새 워크플로우는 매니페스트 등록만으로 늘어납니다.</p>
              <div style={{ marginTop: 18 }}><CardGrid workflows={workflows.filter((w) => !isDevCard(w))} onSelect={tryOpen} canUse={cardCanUse} /></div>
            </>
          ) : (
            /* 홈 = 대시보드 (요약 통계) */
            <>
              <h2 style={{ marginTop: 0 }}>대시보드</h2>
              <p className="muted" style={{ marginTop: 2 }}>안녕하세요{user ? `, ${user.username}` : ""} 👋 — 플랫폼 현황이에요.</p>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 14, marginTop: 18 }}>
                <div className="wf-card" onClick={goCards} style={{ cursor: "pointer" }}>
                  <div className="wf-icon"><Puzzle size={22} strokeWidth={1.9} /></div>
                  <div style={{ fontSize: 34, fontWeight: 800, color: "var(--vsc-fg-strong)", letterSpacing: -1 }}>{cardCount}</div>
                  <div className="wf-name" style={{ marginTop: 2 }}>기능 카드</div>
                  <div className="wf-desc">실행 가능한 단일 기능 (열기 →)</div>
                </div>
                <div className="wf-card" onClick={goPipe} style={{ cursor: "pointer" }}>
                  <div className="wf-icon"><Workflow size={22} strokeWidth={1.9} /></div>
                  <div style={{ fontSize: 34, fontWeight: 800, color: "var(--vsc-fg-strong)", letterSpacing: -1 }}>{pipeCount}</div>
                  <div className="wf-name" style={{ marginTop: 2 }}>자동화 파이프라인</div>
                  <div className="wf-desc">저장된 파이프라인 (열기 →)</div>
                </div>
                <div className="wf-card" style={{ cursor: "default" }}>
                  <div className="wf-icon"><Activity size={22} strokeWidth={1.9} /></div>
                  <div style={{ fontSize: 34, fontWeight: 800, color: "var(--vsc-fg-strong)", letterSpacing: -1 }}>{activeUsers}</div>
                  <div className="wf-name" style={{ marginTop: 2 }}>접속 중 사용자</div>
                  <div className="wf-desc">최근 5분 내 활동</div>
                </div>
              </div>
              <div style={{ marginTop: 22 }}>
                <span style={{ display: "inline-flex", gap: 8 }}>
                  <button onClick={goCards} style={{ display: "inline-flex", alignItems: "center", gap: 7 }}><Puzzle size={16} strokeWidth={2} /> 기능 카드 열기</button>
                  <button className="ghost" onClick={goPipe} style={{ display: "inline-flex", alignItems: "center", gap: 7 }}><Workflow size={16} strokeWidth={2} /> 파이프라인</button>
                </span>
              </div>
            </>
          )}
        </div>
      </div>
    </main>
  );
}
