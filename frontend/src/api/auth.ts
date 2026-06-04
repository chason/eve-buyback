import { apiGet } from "./client"

export type Role = "member" | "manager" | "ceo"

export interface SessionUser {
  character_id: number
  character_name: string
  corporation_id: number
  corporation_name: string
  role: Role
}

export interface LoginUrlResponse {
  authorization_url: string
  state: string
}

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api/v1"

/** Returns the current user, or null when not authenticated (401). */
export async function getMe(): Promise<SessionUser | null> {
  const res = await fetch(`${API_BASE}/auth/me`, { credentials: "include" })
  if (res.status === 401) return null
  if (!res.ok) throw new Error(`API ${res.status}: ${res.statusText}`)
  return (await res.json()) as SessionUser
}

export const getLoginUrl = () => apiGet<LoginUrlResponse>("/auth/login-url")

export async function login(code: string, state: string): Promise<SessionUser> {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code, state }),
  })
  if (!res.ok) throw new Error(`Login failed: ${res.status}`)
  return (await res.json()) as SessionUser
}

export async function logout(): Promise<void> {
  await fetch(`${API_BASE}/auth/logout`, {
    method: "POST",
    credentials: "include",
  })
}
