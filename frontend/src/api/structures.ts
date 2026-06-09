import { apiGet, apiSend } from "./client"
import type { StructureAuthorizeResponse, StructureTokenStatus } from "./types"

export type { StructureTokenStatus } from "./types"

/** The corp's structure-market authorization status (ADR-0029). */
export const getStructureStatus = () =>
  apiGet<StructureTokenStatus>("/corporations/me/structure-token")

/** Begin the structure-access grant: returns the EVE SSO URL to redirect to. */
export async function beginStructureAuthorize(): Promise<StructureAuthorizeResponse> {
  const res = await apiSend("POST", "/corporations/me/structure-token/authorize")
  if (!res.ok) throw new Error(`Authorize start failed: ${res.status}`)
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
  if (!res.ok) throw new Error(`Authorize failed: ${res.status}`)
  return (await res.json()) as StructureTokenStatus
}

/** Revoke the corp's structure authorization. */
export async function revokeStructure(): Promise<void> {
  const res = await apiSend("DELETE", "/corporations/me/structure-token")
  if (!res.ok) throw new Error(`Revoke failed: ${res.status}`)
}

// Set before redirecting to the structure SSO flow so the shared /auth/callback
// knows to complete a structure grant rather than a login.
export const STRUCTURE_AUTH_FLAG = "buyback_structure_authorize"
