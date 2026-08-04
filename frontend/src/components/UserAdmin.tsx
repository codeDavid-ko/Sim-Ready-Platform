"use client";

import { useEffect, useState } from "react";
import { API_BASE } from "@/lib/api";
import { authHeaders } from "@/lib/auth";

type User = { username: string; role: string; created: number; cards?: string[] };
type CardOpt = { id: string; name: string };

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
  const [nc, setNc] = useState<string[]>([]);            // 새 사용자 허용 카드
  // 비번 변경
  const [pwFor, setPwFor] = useState<string | null>(null);
  const [pwVal, setPwVal] = useState("");
  // 카드 권한
  const [availCards, setAvailCards] = useState<CardOpt[]>([]);
  const [cardsFor, setCardsFor] = useState<string | null>(null);
  const [cardsEdit, setCardsEdit] = useState<string[]>([]);
  const toggle = (arr: string[], id: string) => arr.includes(id) ? arr.filter((x) => x !== id) : [...arr, id];
  // 외부 공개 터널
  const [tunnel, setTunnel] = useState<{ running: boolean; url: string | null; error: string | null; installed: boolean } | null>(null);
  const [tunnelBusy, setTunnelBusy] = useState(false);

  async function load() {
    setError(null);
    try {
      const r = await adminJson<{ users: User[]; me: string }>("/api/admin/users");
      setUsers(r.users);
      setMe(r.me);
      const wf = await adminJson<{ workflows: { id: string; name: string; hidden?: boolean }[] }>("/api/workflows");
      setAvailCards(wf.workflows.filter((w) => !w.hidden).map((w) => ({ id: w.id, name: w.name })));
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
      await adminJson("/api/admin/users", { method: "POST", body: JSON.stringify({ username: nu, password: np, role: nr, cards: nr === "user" ? nc : [] }) });
      setNu(""); setNp(""); setNr("user"); setNc([]);
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
  async function saveCards() {
    if (!cardsFor) return;
    setError(null);
    try {
      await adminJson(`/api/admin/users/${encodeURIComponent(cardsFor)}/cards`, { method: "POST", body: JSON.stringify({ cards: cardsEdit }) });
      flash(`${cardsFor} 카드 권한을 저장했습니다.`);
      setCardsFor(null); setCardsEdit([]); load();
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
          {nr === "user" && (
            <div style={{ marginTop: 10 }}>
              <label style={{ fontSize: 13 }}>허용 카드 (체크한 카드만 사용 가능 · admin은 항상 전체)</label>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 4 }}>
                {availCards.map((c) => (
                  <label key={c.id} style={{ fontSize: 12.5, display: "inline-flex", alignItems: "center", gap: 4, border: "1px solid var(--vsc-border)", borderRadius: 8, padding: "2px 8px", cursor: "pointer" }}>
                    <input type="checkbox" checked={nc.includes(c.id)} onChange={() => setNc((a) => toggle(a, c.id))} /> {c.name}
                  </label>
                ))}
              </div>
            </div>
          )}
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
                      <button className="ghost" onClick={() => { setCardsFor(u.username); setCardsEdit(u.cards ?? []); }} disabled={u.role === "admin"} title={u.role === "admin" ? "admin은 전체 사용" : ""}>카드 권한{u.role === "admin" ? "(전체)" : ` (${(u.cards ?? []).length})`}</button>
                      {u.username !== me && <button className="ghost" onClick={() => del(u.username)}>삭제</button>}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted" style={{ marginTop: 8 }}>역할 드롭다운으로 admin/user 전환. 마지막 관리자는 강등·삭제 불가. 자기 자신은 삭제 불가. <b>카드 권한</b>은 user 계정에만(admin은 전체). 프론트 표시용 — 없는 카드는 🔒로 표시되고 열 때 차단됩니다.</p>
        {cardsFor && (
          <div style={{ marginTop: 10, padding: "10px 12px", border: "1px solid var(--vsc-focus)", borderRadius: 8 }}>
            <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
              <b>{cardsFor} — 카드 권한</b>
              <span className="row" style={{ gap: 6 }}>
                <button className="ghost" onClick={() => setCardsEdit(availCards.map((c) => c.id))}>전체</button>
                <button className="ghost" onClick={() => setCardsEdit([])}>해제</button>
              </span>
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 8 }}>
              {availCards.map((c) => (
                <label key={c.id} style={{ fontSize: 12.5, display: "inline-flex", alignItems: "center", gap: 4, border: "1px solid var(--vsc-border)", borderRadius: 8, padding: "2px 8px", cursor: "pointer" }}>
                  <input type="checkbox" checked={cardsEdit.includes(c.id)} onChange={() => setCardsEdit((a) => toggle(a, c.id))} /> {c.name}
                </label>
              ))}
            </div>
            <div className="row" style={{ gap: 8, marginTop: 10 }}>
              <button onClick={saveCards}>저장</button>
              <button className="ghost" onClick={() => { setCardsFor(null); setCardsEdit([]); }}>취소</button>
            </div>
          </div>
        )}
      </div>
    </>
  );
}
