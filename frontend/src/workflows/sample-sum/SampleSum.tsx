"use client";

import { useState } from "react";
import { API_BASE } from "@/lib/api";
import { authHeaders } from "@/lib/auth";
import type { WorkflowModuleProps } from "../registry";

export default function SampleSum({ manifest }: WorkflowModuleProps) {
  const [n, setN] = useState("10");
  const [note, setNote] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<unknown>(null);

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
      const res = await fetch(`${API_BASE}/api/workflows/${manifest.id}/run`, {
        method: "POST",
        headers: authHeaders(),
        body: fd,
      });
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

  return (
    <>
      <div className="card">
        <p className="muted">{manifest.description}</p>
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
    </>
  );
}
