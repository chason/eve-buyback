// Thin fetch wrapper. In dev the Vite proxy forwards /api to the backend;
// in prod the backend serves the SPA from the same origin (ADR-0012).
// `credentials: "include"` sends the session cookie (ADR-0004).
const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api/v1"

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { credentials: "include" })
  if (!res.ok) {
    throw new Error(`API ${res.status}: ${res.statusText}`)
  }
  return (await res.json()) as T
}
