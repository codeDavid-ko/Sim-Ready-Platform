"use client";

import { useEffect, useState } from "react";
import { apiJson } from "@/lib/api";
import { getToken, setToken } from "@/lib/auth";
import { CardGrid } from "@/components/CardGrid";
import { WorkflowContainer } from "@/components/WorkflowContainer";
import type { WorkflowManifest } from "@/workflows/registry";

type Status = { auth_required: boolean };

// 셸(Shell) — 역할은 딱 3가지: 인증 / 레지스트리(카드 그리드) / 마운트(컨테이너).
// 워크플로우 내부 UI/로직은 모른다.
export default function Home() {
  const [authRequired, setAuthRequired] = useState(false);
  const [authed, setAuthed] = useState(false);
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  const [workflows, setWorkflows] = useState<WorkflowManifest[]>([]);
  const [selected, setSelected] = useState<WorkflowManifest | null>(null);

  // 인증 상태 확인 (SCR-01)
  useEffect(() => {
    apiJson<Status>("/api/status")
      .then((s) => {
        setAuthRequired(s.auth_required);
        if (!s.auth_required || getToken()) setAuthed(true);
      })
      .catch(() => {});
  }, []);

  // 레지스트리 로드 (SCR-02)
  useEffect(() => {
    if (!authed) return;
    apiJson<{ workflows: WorkflowManifest[] }>("/api/workflows")
      .then((r) => setWorkflows(r.workflows))
      .catch(() => {});
  }, [authed]);

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

  // SCR-01 로그인
  if (authRequired && !authed) {
    return (
      <main className="wrap">
        <div className="card">
          <h2>Sim-ready Platform</h2>
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
      <h2>Sim-ready Platform</h2>
      <p className="muted">워크플로우를 골라 실행하세요. 새 워크플로우는 매니페스트 등록만으로 카드가 늘어납니다.</p>
      {selected ? (
        // SCR-03 마운트
        <WorkflowContainer manifest={selected} onBack={() => setSelected(null)} />
      ) : (
        // SCR-02 카드 그리드
        <CardGrid workflows={workflows} onSelect={setSelected} />
      )}
    </main>
  );
}
