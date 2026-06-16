// Thin fetch wrapper. In dev the Vite proxy forwards /api to the backend;
// in prod the backend serves the SPA from the same origin (ADR-0012).
// `credentials: "include"` sends the session cookie (ADR-0004).
const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api/v1"

// Custom header required by the backend on state-changing requests (ADR-0017).
// A cross-origin attacker can't set it without a CORS preflight we don't grant.
const CSRF_HEADER = "X-Buyback-CSRF"

/** Friendly fallback text when a failed response carries no usable `detail`. */
function fallbackMessage(status: number, context: string): string {
  if (status === 401) return "Your session has expired. Please log in again."
  if (status === 403) return "You don't have permission to do that."
  if (status === 422) return `${context} — please check your input and try again.`
  if (status >= 500) return "Something went wrong on our end. Please try again."
  return `${context} (error ${status}).`
}

/** Throw an Error carrying the backend's human-readable `{detail}` when present,
 * otherwise a friendly per-status fallback. The backend returns rich, user-facing
 * detail strings for its errors (application/errors.py); FastAPI request-validation
 * errors instead put an array in `detail`, so only a string is surfaced as-is.
 * `context` names the failed action, e.g. "Save config failed". */
export async function throwApiError(
  res: Response,
  context: string,
): Promise<never> {
  let detail: string | undefined
  try {
    const body = (await res.json()) as { detail?: unknown }
    if (typeof body?.detail === "string" && body.detail.trim()) {
      detail = body.detail
    }
  } catch {
    // Empty or non-JSON body — fall back to the status-based message below.
  }
  throw new Error(detail ?? fallbackMessage(res.status, context))
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { credentials: "include" })
  if (!res.ok) await throwApiError(res, "Request failed")
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
