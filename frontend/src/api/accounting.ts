import { apiGet, apiSend, throwApiError } from "./client"
import type { HangarOut, InventoryOut } from "./types"

export type {
  HangarOut,
  InventoryItemOut,
  InventoryLotOut,
  InventoryOut,
} from "./types"

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api/v1"

/** The inventory fetch distinguishes "no access yet" (402, ADR-0042) from real
 * errors, so the page can show the how-to-pay panel instead of an error blurb. */
export type InventoryResult =
  | { access: true; inventory: InventoryOut }
  | { access: false }

export const getInventory = async (): Promise<InventoryResult> => {
  const res = await fetch(`${API_BASE}/corporations/me/accounting/inventory`, {
    credentials: "include",
  })
  if (res.status === 402) return { access: false }
  if (!res.ok) throw new Error(`Couldn't load inventory (error ${res.status}).`)
  return { access: true, inventory: (await res.json()) as InventoryOut }
}

/** The corp hangar divisions counted as buyback stock (ADR-0044). Reached only
 * from the Stock page, which has already resolved the 402 question. */
export const listHangars = () =>
  apiGet<HangarOut[]>("/corporations/me/accounting/hangars")

export async function addHangar(
  locationId: string,
  division: number,
): Promise<HangarOut> {
  const res = await apiSend("POST", "/corporations/me/accounting/hangars", {
    location_id: locationId,
    division,
  })
  if (!res.ok) await throwApiError(res, "Adding the hangar failed")
  return (await res.json()) as HangarOut
}

export async function removeHangar(
  locationId: string,
  division: number,
): Promise<void> {
  const res = await apiSend(
    "DELETE",
    `/corporations/me/accounting/hangars/${locationId}/${division}`,
  )
  if (!res.ok) await throwApiError(res, "Removing the hangar failed")
}
