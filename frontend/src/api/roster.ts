import { apiGet, apiSend, throwApiError } from "./client"
import type { CorpMemberOut, RosterStatusOut } from "./types"

export type { CorpMemberOut, RosterStatusOut } from "./types"

/** The corp roster's sync status — whether it's been pulled, when, and how many
 * members (ADR-0036). CEO/Director only. */
export const getRosterStatus = () =>
  apiGet<RosterStatusOut>("/corporations/me/roster")

/** Re-pull the corp roster with the stored Corp ESI token (server-side, no EVE
 * round-trip). Rate-limited: a 429 means it was refreshed too recently. */
export async function refreshRoster(): Promise<RosterStatusOut> {
  const res = await apiSend("POST", "/corporations/me/roster/refresh")
  if (!res.ok) await throwApiError(res, "Roster refresh failed")
  return (await res.json()) as RosterStatusOut
}

/** Search the synced roster by name for the manager-designation picker. */
export const searchMembers = (q: string) =>
  apiGet<CorpMemberOut[]>(
    `/corporations/me/roster/members?q=${encodeURIComponent(q)}`,
  )
