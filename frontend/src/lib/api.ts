import { authHeaders } from "./auth";

// same-origin("") 기본 — /api/* 는 next.config rewrites 로 백엔드에 전달된다.
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

export async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...authHeaders(), ...(init?.headers ?? {}) },
  });
  if (!res.ok) throw new Error(`${path} ${res.status}`);
  return (await res.json()) as T;
}

// 긴 워크플로우: 잡 제출 후 폴링(프록시 타임아웃 회피). 결과 dict 반환.
export async function submitAndPoll<T>(
  submitPath: string,
  fd: FormData,
  onTick?: (status: string) => void,
  intervalMs = 3000,
): Promise<T> {
  const res = await fetch(`${API_BASE}${submitPath}`, { method: "POST", headers: authHeaders(), body: fd });
  if (!res.ok) {
    let d = `오류 (${res.status})`;
    try { d = (await res.json()).detail ?? d; } catch {}
    throw new Error(d);
  }
  const { job_id } = await res.json();
  for (;;) {
    await new Promise((r) => setTimeout(r, intervalMs));
    const jr = await fetch(`${API_BASE}/api/workflows/jobs/${job_id}`, { headers: authHeaders() });
    if (!jr.ok) throw new Error(`잡 상태 조회 실패 (${jr.status})`);
    const j = await jr.json();
    onTick?.(j.status);
    if (j.status === "done") return j.result as T;
    if (j.status === "error") throw new Error(j.error || "작업이 실패했습니다.");
    if (j.status === "unknown") throw new Error("작업을 찾을 수 없습니다(서버 재시작?).");
  }
}

// 인증 헤더로 받아 object URL 로 돌려준다(뷰어 src 등). 호출측에서 revoke 책임.
export async function blobUrl(path: string): Promise<string> {
  const res = await fetch(`${API_BASE}${path}`, { headers: { ...authHeaders() } });
  if (!res.ok) throw new Error(`${path} ${res.status}`);
  return URL.createObjectURL(await res.blob());
}

// 다운로드는 <a href> 로는 토큰을 못 실으므로 fetch + Authorization 후 blob 으로 저장한다.
export async function downloadFile(path: string, filename: string): Promise<void> {
  const res = await fetch(`${API_BASE}${path}`, { headers: { ...authHeaders() } });
  if (!res.ok) throw new Error(`${path} ${res.status}`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
