import { apiGet, apiSend, throwApiError } from "./client"
import type { ManagerOut } from "./types"

export type { ManagerOut } from "./types"

/** The corp's current Buyback Managers (ADR-0036). CEO/Director only. */
export const listManagers = () =>
  apiGet<ManagerOut[]>("/corporations/me/managers")

/** Designate a character as a Buyback Manager. The server re-checks corp
 * membership at ESI before granting. */
export async function addManager(characterId: number): Promise<ManagerOut> {
  const res = await apiSend("POST", "/corporations/me/managers", {
    character_id: characterId,
  })
  if (!res.ok) await throwApiError(res, "Granting manager failed")
  return (await res.json()) as ManagerOut
}

/** Revoke a character's Buyback Manager role. */
export async function removeManager(characterId: number): Promise<void> {
  const res = await apiSend("DELETE", `/corporations/me/managers/${characterId}`)
  if (!res.ok) await throwApiError(res, "Removing manager failed")
}
