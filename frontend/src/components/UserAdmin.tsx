"use client";

import { useEffect, useState } from "react";
import { API_BASE } from "@/lib/api";
import { authHeaders } from "@/lib/auth";

type User = { username: string; role: string; created: number };

async function adminJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...authHeaders(), ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let d = `오류 (${res.status})`;
    try { d = (await res.json()).detail ?? d; } catch { /* noop */ }
    throw new Error(d);
  }
  return (await res.json()) as T;
}

export function UserAdmin({ onBack }: { onBack: () => void }) {
  const [users, setUsers] = useState<User[]>([]);
  const [me, setMe] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  // 새 사용자 폼
  const [nu, setNu] = useState("");
  const [np, setNp] = useState("");
  const [nr, setNr] = useState("user");
  // 비번 변경
  const [pwFor, setPwFor] = useState<string | null>(null);
  const [pwVal, setPwVal] = useState("");

  async function load() {
    setError(null);
    try {
      const r = await adminJson<{ users: User[]; me: string }>("/api/admin/users");
      setUsers(r.users);
      setMe(r.me);
    } catch (e) { setError(String((e as Error).message)); }
  }
  useEffect(() => { load(); }, []);

  function flash(m: string) { setMsg(m); setTimeout(() => setMsg(null), 2500); }

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await adminJson("/api/admin/users", { method: "POST", body: JSON.stringify({ username: nu, password: np, role: nr }) });
      setNu(""); setNp(""); setNr("user");
      flash("사용자를 추가했습니다.");
      load();
    } catch (e) { setError(String((e as Error).message)); }
  }
  async function resetPw(username: string) {
    if (!pwVal) { setError("새 비밀번호를 입력하세요."); return; }
    setError(null);
    try {
      await adminJson(`/api/admin/users/${encodeURIComponent(username)}/password`, { method: "POST", body: JSON.stringify({ password: pwVal }) });
      setPwFor(null); setPwVal("");
      flash(`${username} 비밀번호를 변경했습니다.`);
    } catch (e) { setError(String((e as Error).message)); }
  }
  async function changeRole(username: string, role: string) {
    setError(null);
    try {
      await adminJson(`/api/admin/users/${encodeURIComponent(username)}/role`, { method: "POST", body: JSON.stringify({ role }) });
      flash(`${username} 역할을 ${role}로 변경했습니다.`);
      load();
    } catch (e) { setError(String((e as Error).message)); }
  }
  async function del(username: string) {
    if (!window.confirm(`${username} 사용자를 삭제할까요?`)) return;
    setError(null);
    try {
      await adminJson(`/api/admin/users/${encodeURIComponent(username)}`, { method: "DELETE" });
      flash(`${username} 삭제됨.`);
      load();
    } catch (e) { setError(String((e as Error).message)); }
  }

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <button className="ghost" onClick={onBack}>← 돌아가기</button>
        <h3 style={{ margin: 0 }}>사용자 관리</h3>
        <span />
      </div>

      {error && <div className="card"><p className="err">{error}</p></div>}
      {msg && <div className="card"><p className="muted">✓ {msg}</p></div>}

      <div className="card">
        <label>새 사용자 추가</label>
        <form onSubmit={create}>
          <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
            <div style={{ flex: 1, minWidth: 140 }}>
              <label>아이디</label>
              <input value={nu} onChange={(e) => setNu(e.target.value)} placeholder="아이디" />
            </div>
            <div style={{ flex: 1, minWidth: 140 }}>
              <label>비밀번호</label>
              <input type="text" value={np} onChange={(e) => setNp(e.target.value)} placeholder="초기 비밀번호" />
            </div>
            <div>
              <label>역할</label>
              <select value={nr} onChange={(e) => setNr(e.target.value)}>
                <option value="user">user</option>
                <option value="admin">admin</option>
              </select>
            </div>
            <button type="submit">추가</button>
          </div>
        </form>
      </div>

      <div className="card">
        <label>사용자 목록 ({users.length})</label>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "2px solid #e3e6ea" }}>
              <th style={{ padding: "6px 8px" }}>아이디</th>
              <th style={{ padding: "6px 8px" }}>역할</th>
              <th style={{ padding: "6px 8px" }}>관리</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.username} style={{ borderBottom: "1px solid #eef0f2" }}>
                <td style={{ padding: "6px 8px", fontWeight: 600 }}>
                  {u.username}{u.username === me ? <span className="muted" style={{ fontSize: 11 }}> (나)</span> : null}
                </td>
                <td style={{ padding: "6px 8px" }}>
                  <select value={u.role} onChange={(e) => changeRole(u.username, e.target.value)}>
                    <option value="user">user</option>
                    <option value="admin">admin</option>
                  </select>
                </td>
                <td style={{ padding: "6px 8px" }}>
                  {pwFor === u.username ? (
                    <span className="row" style={{ gap: 6 }}>
                      <input type="text" value={pwVal} onChange={(e) => setPwVal(e.target.value)} placeholder="새 비밀번호" style={{ width: 140 }} />
                      <button className="ghost" onClick={() => resetPw(u.username)}>저장</button>
                      <button className="ghost" onClick={() => { setPwFor(null); setPwVal(""); }}>취소</button>
                    </span>
                  ) : (
                    <span className="row" style={{ gap: 6 }}>
                      <button className="ghost" onClick={() => { setPwFor(u.username); setPwVal(""); }}>비밀번호 변경</button>
                      {u.username !== me && <button className="ghost" onClick={() => del(u.username)}>삭제</button>}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted" style={{ marginTop: 8 }}>역할 드롭다운으로 admin/user 전환. 마지막 관리자는 강등·삭제 불가. 자기 자신은 삭제 불가.</p>
      </div>
    </>
  );
}
