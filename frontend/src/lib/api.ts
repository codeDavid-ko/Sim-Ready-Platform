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

export class CancelledError extends Error {
  constructor() { super("취소됨"); this.name = "CancelledError"; }
}

/** 실행 중 잡 취소 요청(자식 프로세스까지 종료). best-effort. */
export async function cancelJob(jobId: string): Promise<void> {
  try {
    await fetch(`${API_BASE}/api/workflows/jobs/${jobId}/cancel`, { method: "POST", headers: authHeaders() });
  } catch { /* best-effort */ }
}

// 긴 워크플로우: 잡 제출 후 폴링(프록시 타임아웃 회피). 결과 dict 반환.
// 3번째 인자는 onTick 함수(하위호환) 또는 옵션 객체 { signal?, onTick? }.
// signal(AbortSignal)을 주면 abort 시 잡 취소를 호출하고 CancelledError 를 던진다.
export async function submitAndPoll<T>(
  submitPath: string,
  fd: FormData,
  optsOrOnTick?: ((status: string) => void) | { signal?: AbortSignal; onTick?: (status: string) => void; onProgress?: (p: { phase?: string; frame?: number; total?: number } | null) => void },
  intervalMs = 3000,
): Promise<T> {
  const opts = typeof optsOrOnTick === "function" ? { onTick: optsOrOnTick } : (optsOrOnTick ?? {});
  const { signal, onTick, onProgress } = opts;

  const res = await fetch(`${API_BASE}${submitPath}`, { method: "POST", headers: authHeaders(), body: fd, signal });
  if (!res.ok) {
    let d = `오류 (${res.status})`;
    try { d = (await res.json()).detail ?? d; } catch {}
    throw new Error(d);
  }
  const { job_id } = await res.json();
  try {
    for (;;) {
      await new Promise((r) => setTimeout(r, intervalMs));
      if (signal?.aborted) { await cancelJob(job_id); throw new CancelledError(); }
      const jr = await fetch(`${API_BASE}/api/workflows/jobs/${job_id}`, { headers: authHeaders(), signal });
      if (!jr.ok) throw new Error(`잡 상태 조회 실패 (${jr.status})`);
      const j = await jr.json();
      onTick?.(j.status);
      // 파이프라인은 별도 슬롯(pipe)의 스텝 진행률을 우선 사용(내부 카드가 progress 를 덮어써도 보존).
      onProgress?.(j.pipe ?? j.progress ?? null);
      if (j.status === "done") return j.result as T;
      if (j.status === "cancelled") throw new CancelledError();
      if (j.status === "error") throw new Error(j.error || "작업이 실패했습니다.");
      if (j.status === "unknown") throw new Error("작업을 찾을 수 없습니다(서버 재시작?).");
    }
  } catch (e) {
    // signal abort 로 fetch 가 던진 경우에도 잡 취소를 보장
    if (signal?.aborted) { await cancelJob(job_id); throw new CancelledError(); }
    throw e;
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

// 자기완결 다운로드 — /download → /portable 로 바꿔 외부의존(MDL/텍스처/레퍼런스)을 묶은 usdz 를 받는다.
// USD 산출물은 이걸로 받아야 다른 PC 에서 형상+재질이 안 깨진다. 서버가 자기완결이면 원본을, 아니면 usdz 를
// 주므로 실제 파일명은 서버 응답(Content-Disposition)을 따른다.
const _USD_DL = /\.(usd|usda|usdc)$/i;
export async function downloadAsset(downloadUrl: string, filename: string): Promise<void> {
  if (!_USD_DL.test(filename)) return downloadFile(downloadUrl, filename);
  const portable = downloadUrl.replace(/\/download$/, "/portable");
  const res = await fetch(`${API_BASE}${portable}`, { headers: { ...authHeaders() } });
  if (!res.ok) throw new Error(`${portable} ${res.status}`);
  const cd = res.headers.get("content-disposition") || "";
  const m = cd.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
  const fn = m ? decodeURIComponent(m[1]) : filename;
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = fn;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}
