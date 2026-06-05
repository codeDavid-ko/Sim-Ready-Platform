"use client";

import { useEffect, useState } from "react";
import { apiJson } from "@/lib/api";
import { clearToken, getToken, getUser, setToken, setUser, type SessionUser } from "@/lib/auth";
import { CardGrid } from "@/components/CardGrid";
import { WorkflowContainer } from "@/components/WorkflowContainer";
import { UserAdmin } from "@/components/UserAdmin";
import type { WorkflowManifest } from "@/workflows/registry";

type Status = { auth_required: boolean; needs_setup: boolean };
type LoginResp = { token: string; username: string; role: string };

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
  const [showAdmin, setShowAdmin] = useState(false);

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
      .then((r) => setWorkflows(r.workflows))
      .catch(() => {});
    // 토큰만 있고 사용자 정보 없으면 /me 로 보강
    if (!getUser()) {
      apiJson<{ username: string; role: string }>("/api/me")
        .then((m) => { setUser(m); setUserState(m); })
        .catch(() => {});
    }
  }, [authed]);

  function applyLogin(r: LoginResp) {
    setToken(r.token);
    const u = { username: r.username, role: r.role };
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

  return (
    <main className="wrap">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <h2 style={{ margin: 0 }}>Sim-ready Platform</h2>
        <span className="row" style={{ gap: 10, alignItems: "center" }}>
          {user && <span className="muted">{user.username} · {user.role}</span>}
          {user?.role === "admin" && !showAdmin && (
            <button className="ghost" onClick={() => { setSelected(null); setShowAdmin(true); }}>👤 사용자 관리</button>
          )}
          <button className="ghost" onClick={logout}>로그아웃</button>
        </span>
      </div>

      {showAdmin ? (
        <div className="detail"><UserAdmin onBack={() => setShowAdmin(false)} /></div>
      ) : selected ? (
        // SCR-03 마운트
        <div className="detail"><WorkflowContainer manifest={selected} onBack={() => setSelected(null)} /></div>
      ) : (
        <>
          <p className="muted">워크플로우를 골라 실행하세요. 새 워크플로우는 매니페스트 등록만으로 카드가 늘어납니다.</p>
          {/* SCR-02 카드 그리드 */}
          <CardGrid workflows={workflows} onSelect={setSelected} />
        </>
      )}
    </main>
  );
}
