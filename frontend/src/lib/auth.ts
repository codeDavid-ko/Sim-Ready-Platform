const KEY = "algo_runner_token";
const USER_KEY = "algo_runner_user";

export type SessionUser = { username: string; role: string };

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(KEY);
}
export function setToken(t: string): void {
  if (typeof window !== "undefined") window.localStorage.setItem(KEY, t);
}
export function clearToken(): void {
  if (typeof window !== "undefined") {
    window.localStorage.removeItem(KEY);
    window.localStorage.removeItem(USER_KEY);
  }
}
export function setUser(u: SessionUser): void {
  if (typeof window !== "undefined") window.localStorage.setItem(USER_KEY, JSON.stringify(u));
}
export function getUser(): SessionUser | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try { return JSON.parse(raw) as SessionUser; } catch { return null; }
}
export function authHeaders(): Record<string, string> {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}
