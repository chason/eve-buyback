// Thin fetch wrapper. In dev the Vite proxy forwards /api to the backend;
// in prod the backend serves the SPA from the same origin (ADR-0012).
// `credentials: "include"` sends the session cookie (ADR-0004).
const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api/v1"

// Custom header required by the backend on state-changing requests (ADR-0017).
// A cross-origin attacker can't set it without a CORS preflight we don't grant.
const CSRF_HEADER = "X-Buyback-CSRF"

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { credentials: "include" })
  if (!res.ok) {
    throw new Error(`API ${res.status}: ${res.statusText}`)
  }
  return (await res.json()) as T
}

/** Issue a state-changing request with the CSRF header attached. */
export async function apiSend(
  method: "POST" | "PUT" | "PATCH" | "DELETE",
  path: string,
  body?: unknown,
): Promise<Response> {
  const headers: Record<string, string> = { [CSRF_HEADER]: "1" }
  if (body !== undefined) headers["Content-Type"] = "application/json"
  return fetch(`${API_BASE}${path}`, {
    method,
    credentials: "include",
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
}
