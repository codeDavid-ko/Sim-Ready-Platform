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
