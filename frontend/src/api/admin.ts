import { apiGet, apiSend, throwApiError } from "./client"
import type { CorpAccessOut } from "./types"

export type { CorpAccessOut } from "./types"

/** All registered corps with their accounting-access status (app admin only,
 * ADR-0041/0042). */
export const listCorpAccess = () => apiGet<CorpAccessOut[]>("/admin/access")

/** Grant or extend a corp's access; null `expiresAt` = access never expires. */
export async function grantCorpAccess(
  corporationId: number,
  expiresAt: string | null,
): Promise<CorpAccessOut> {
  const res = await apiSend("PUT", `/admin/access/${corporationId}`, {
    expires_at: expiresAt,
  })
  if (!res.ok) await throwApiError(res, "Giving access failed")
  return (await res.json()) as CorpAccessOut
}

/** Remove a corp's access. */
export async function revokeCorpAccess(corporationId: number): Promise<void> {
  const res = await apiSend("DELETE", `/admin/access/${corporationId}`)
  if (!res.ok) await throwApiError(res, "Removing access failed")
}
