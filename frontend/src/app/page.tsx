"use client";

import { useEffect, useRef, useState } from "react";
import { API_BASE, apiJson } from "@/lib/api";
import { authHeaders, getToken, setToken } from "@/lib/auth";

type Status = { auth_required: boolean };
type Tunnel = { running: boolean; url: string | null; error: string | null; installed: boolean };

export default function Home() {
  const [authRequired, setAuthRequired] = useState(false);
  const [authed, setAuthed] = useState(false);
  const [password, setPassword] = useState("");

  // ★ 입력 필드 — 여기 칸을 늘리면 그대로 백엔드 params 로 전달됩니다.
  const [n, setN] = useState("10");
  const [note, setNote] = useState("");
  const [file, setFile] = useState<File | null>(null);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<unknown>(null);

  const [tunnel, setTunnel] = useState<Tunnel | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    apiJson<Status>("/api/status")
      .then((s) => {
        setAuthRequired(s.auth_required);
        if (!s.auth_required || getToken()) setAuthed(true);
      })
      .catch(() => {});
  }, []);

  async function login(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const r = await apiJson<{ token: string }>("/api/login", {
        method: "POST",
        body: JSON.stringify({ password }),
      });
      setToken(r.token);
      setAuthed(true);
    } catch {
      setError("비밀번호가 올바르지 않습니다.");
    }
  }

  async function run(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const fd = new FormData();
      fd.append("n", n);
      fd.append("note", note);
      if (file) fd.append("file", file);
      const res = await fetch(`${API_BASE}/api/run`, { method: "POST", headers: authHeaders(), body: fd });
      if (!res.ok) {
        let d = `오류 (${res.status})`;
        try { d = (await res.json()).detail ?? d; } catch {}
        setError(d);
        return;
      }
      setResult((await res.json()).result);
    } catch {
      setError("백엔드에 연결할 수 없습니다.");
    } finally {
      setBusy(false);
    }
  }

  async function refreshTunnel() {
    try { setTunnel(await apiJson<Tunnel>("/api/admin/tunnel")); } catch {}
  }
  useEffect(() => {
    if (authed) refreshTunnel();
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [authed]);
  useEffect(() => {
    if (tunnel?.running && !tunnel.url) pollRef.current = setInterval(refreshTunnel, 1500);
    else if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  }, [tunnel?.running, tunnel?.url]);

  async function startTunnel() {
    setTunnel(await apiJson<Tunnel>("/api/admin/tunnel/start", { method: "POST" }));
  }
  async function stopTunnel() {
    setTunnel(await apiJson<Tunnel>("/api/admin/tunnel/stop", { method: "POST" }));
  }

  if (authRequired && !authed) {
    return (
      <main className="wrap">
        <div className="card">
          <h2>algo-runner</h2>
          <form onSubmit={login}>
            <label>비밀번호</label>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoFocus />
            {error && <p className="err">{error}</p>}
            <div style={{ marginTop: 12 }}><button type="submit">들어가기</button></div>
          </form>
        </div>
      </main>
    );
  }

  return (
    <main className="wrap">
      <h2>algo-runner</h2>
      <p className="muted">입력을 넣고 실행하면 백엔드의 알고리즘이 돌아 결과를 돌려줍니다.</p>

      <div className="card">
        <form onSubmit={run}>
          <label>n (숫자 예시 — 1..n 합)</label>
          <input value={n} onChange={(e) => setN(e.target.value)} />
          <label>note (텍스트 예시)</label>
          <input value={note} onChange={(e) => setNote(e.target.value)} />
          <label>파일 (선택)</label>
          <input type="file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          {error && <p className="err">{error}</p>}
          <div style={{ marginTop: 12 }}>
            <button type="submit" disabled={busy}>{busy ? "실행 중…" : "실행"}</button>
          </div>
        </form>
      </div>

      {result != null && (
        <div className="card">
          <label>결과</label>
          <pre>{JSON.stringify(result, null, 2)}</pre>
        </div>
      )}

      <div className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <b>외부 공개</b>
          {tunnel?.running ? (
            <button className="ghost" onClick={stopTunnel}>공개 끄기</button>
          ) : (
            <button className="ghost" onClick={startTunnel} disabled={tunnel?.installed === false}>공개 주소 켜기</button>
          )}
        </div>
        {tunnel?.installed === false && <p className="err">cloudflared 미설치 (winget install Cloudflare.cloudflared)</p>}
        {tunnel?.running && !tunnel.url && <p className="muted">주소 발급 중…</p>}
        {tunnel?.url && (
          <p><a href={tunnel.url} target="_blank" rel="noreferrer">{tunnel.url}</a> — 이 주소를 알려주면 됩니다.</p>
        )}
      </div>
    </main>
  );
}
