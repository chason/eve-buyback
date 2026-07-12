import type { InventoryOut } from "./types"

export type { InventoryItemOut, InventoryLotOut, InventoryOut } from "./types"

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
