import { apiGet, apiSend, throwApiError } from "./client"
import type {
  StructureAuthorizeResponse,
  StructureSearchResult,
  StructureTokenStatus,
} from "./types"

export type { StructureSearchResult, StructureTokenStatus } from "./types"

/** The corp's structure-market authorization status (ADR-0029). */
export const getStructureStatus = () =>
  apiGet<StructureTokenStatus>("/corporations/me/structure-token")

/** Search the corp's accessible structures by name (requires authorization first). */
export const searchStructures = (q: string) =>
  apiGet<StructureSearchResult[]>(
    `/corporations/me/structure-token/search?q=${encodeURIComponent(q)}`,
  )

/** Begin the structure-access grant: returns the EVE SSO URL to redirect to. */
export async function beginStructureAuthorize(): Promise<StructureAuthorizeResponse> {
  const res = await apiSend("POST", "/corporations/me/structure-token/authorize")
  if (!res.ok) await throwApiError(res, "Authorize start failed")
  return (await res.json()) as StructureAuthorizeResponse
}

/** Complete the grant from the SSO callback. */
export async function completeStructureAuthorize(
  code: string,
  state: string,
): Promise<StructureTokenStatus> {
  const res = await apiSend("POST", "/corporations/me/structure-token/session", {
    code,
    state,
  })
  if (!res.ok) await throwApiError(res, "Authorize failed")
  return (await res.json()) as StructureTokenStatus
}

/** Revoke the corp's structure authorization. */
export async function revokeStructure(): Promise<void> {
  const res = await apiSend("DELETE", "/corporations/me/structure-token")
  if (!res.ok) await throwApiError(res, "Revoke failed")
}

// The login and structure flows share one /auth/callback. We route on the OAuth
// `state` echoed back by EVE — the structure flow's state carries this prefix
// (set server-side). This is reliable across the redirect, unlike sessionStorage,
// which could be lost and misroute the structure round-trip to the login endpoint
// (→ a 400 "Invalid or expired OAuth state"). Login states never contain ".".
export const STRUCTURE_STATE_PREFIX = "structure."
