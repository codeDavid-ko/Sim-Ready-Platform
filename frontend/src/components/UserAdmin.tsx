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
  // 외부 공개 터널
  const [tunnel, setTunnel] = useState<{ running: boolean; url: string | null; error: string | null; installed: boolean } | null>(null);
  const [tunnelBusy, setTunnelBusy] = useState(false);

  async function load() {
    setError(null);
    try {
      const r = await adminJson<{ users: User[]; me: string }>("/api/admin/users");
      setUsers(r.users);
      setMe(r.me);
    } catch (e) { setError(String((e as Error).message)); }
  }
  type Tun = { running: boolean; url: string | null; error: string | null; installed: boolean };
  async function loadTunnel() {
    try { setTunnel(await adminJson<Tun>("/api/admin/tunnel")); } catch { /* */ }
  }
  useEffect(() => { load(); loadTunnel(); }, []);

  function flash(m: string) { setMsg(m); setTimeout(() => setMsg(null), 2500); }

  async function startTunnel() {
    setError(null); setTunnelBusy(true);
    try {
      let st = await adminJson<Tun>("/api/admin/tunnel/start", { method: "POST" });
      setTunnel(st);
      // URL 은 cloudflared 로그에서 몇 초 뒤 잡힘 → 잠깐 폴링
      for (let i = 0; i < 15 && st.running && !st.url; i++) {
        await new Promise((r) => setTimeout(r, 1500));
        st = await adminJson<Tun>("/api/admin/tunnel");
        setTunnel(st);
      }
      if (st.error) setError(st.error);
    } catch (e) { setError(String((e as Error).message)); }
    finally { setTunnelBusy(false); }
  }
  async function stopTunnel() {
    setTunnelBusy(true);
    try { setTunnel(await adminJson<Tun>("/api/admin/tunnel/stop", { method: "POST" })); }
    catch (e) { setError(String((e as Error).message)); }
    finally { setTunnelBusy(false); }
  }

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
        <div className="row" style={{ justifyContent: "space-between" }}>
          <label style={{ margin: 0 }}>외부 공개 (Cloudflare 터널)</label>
          {tunnel && <span className={`badge ${tunnel.running ? "ok" : ""}`}>{tunnel.running ? "켜짐" : "꺼짐"}</span>}
        </div>
        {tunnel && !tunnel.installed && <p className="err">cloudflared 가 설치되어 있지 않습니다.</p>}
        {tunnel?.running && tunnel.url && (
          <div style={{ marginTop: 8 }}>
            <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
              <a href={tunnel.url} target="_blank" rel="noreferrer" style={{ fontWeight: 600, wordBreak: "break-all" }}>{tunnel.url}</a>
              <button className="ghost" onClick={() => { navigator.clipboard?.writeText(tunnel.url!); flash("URL 복사됨"); }}>복사</button>
            </div>
            <p className="muted" style={{ marginTop: 6 }}>이 주소를 공유하면 외부에서 접속합니다. 로그인(아이디/비밀번호)으로 보호됩니다. 임시 주소라 끄면 사라지고, 다시 켜면 새 주소가 생깁니다.</p>
          </div>
        )}
        {tunnel?.running && !tunnel.url && tunnelBusy && <p className="muted" style={{ marginTop: 6 }}>주소 받는 중…</p>}
        {tunnel?.error && <p className="err">{tunnel.error}</p>}
        <div className="row" style={{ marginTop: 10, gap: 8 }}>
          {tunnel?.running ? (
            <button className="ghost" onClick={stopTunnel} disabled={tunnelBusy}>{tunnelBusy ? "처리 중…" : "외부 공개 끄기"}</button>
          ) : (
            <button onClick={startTunnel} disabled={tunnelBusy || (tunnel ? !tunnel.installed : false)}>{tunnelBusy ? "켜는 중… (주소 생성)" : "외부 공개 켜기"}</button>
          )}
        </div>
      </div>

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
