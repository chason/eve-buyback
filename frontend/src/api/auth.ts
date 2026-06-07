import { apiSend } from "./client"
import type { CorporationOut, LoginUrlResponse, SessionUser } from "./types"

// Re-export the generated types so call sites can import them from here.
export type { CorporationOut, LoginUrlResponse, Role, SessionUser } from "./types"

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api/v1"

/** Returns the current user, or null when not authenticated (401). */
export async function getMe(): Promise<SessionUser | null> {
  const res = await fetch(`${API_BASE}/auth/me`, { credentials: "include" })
  if (res.status === 401) return null
  if (!res.ok) throw new Error(`API ${res.status}: ${res.statusText}`)
  return (await res.json()) as SessionUser
}

/** Begin login: returns the EVE authorize URL to redirect the browser to. */
export async function beginLogin(): Promise<LoginUrlResponse> {
  const res = await apiSend("POST", "/auth/login")
  if (!res.ok) throw new Error(`Login start failed: ${res.status}`)
  return (await res.json()) as LoginUrlResponse
}

export async function login(code: string, state: string): Promise<SessionUser> {
  const res = await apiSend("POST", "/auth/session", { code, state })
  if (!res.ok) throw new Error(`Login failed: ${res.status}`)
  return (await res.json()) as SessionUser
}

export async function logout(): Promise<void> {
  await apiSend("DELETE", "/auth/session")
}

export async function registerCorporation(): Promise<CorporationOut> {
  const res = await apiSend("POST", "/corporations")
  if (!res.ok) throw new Error(`Register failed: ${res.status}`)
  return (await res.json()) as CorporationOut
}
