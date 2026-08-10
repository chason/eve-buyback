import { apiGet, apiSend, throwApiError } from "./client"
import type {
  HangarCheckResult,
  HangarOut,
  InventoryOut,
  ManualExpenseRequest,
  ManualLotRequest,
  ManualSaleRequest,
  ProfitOut,
  ReceivableOut,
  ReconciliationEventOut,
  ReprocessPreviewOut,
  ReprocessResultOut,
} from "./types"

export type {
  ChannelProfitOut,
  HangarCheckResult,
  HangarOut,
  InventoryItemOut,
  InventoryLotOut,
  InventoryOut,
  ManualExpenseRequest,
  ManualLotRequest,
  ManualSaleRequest,
  ProfitOut,
  ReceivableOut,
  ReconciliationEventOut,
  ReprocessPreviewOut,
  ReprocessResultOut,
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

/** The "How we're doing" profit fetch (#159) — same 402 split as the inventory,
 * so the page can show the how-to-pay panel instead of an error blurb. */
export type ProfitResult =
  | { access: true; profit: ProfitOut }
  | { access: false }

export const getProfit = async (
  since: string | null,
  until: string | null,
): Promise<ProfitResult> => {
  const params = new URLSearchParams()
  if (since) params.set("since", since)
  if (until) params.set("until", until)
  const query = params.size > 0 ? `?${params.toString()}` : ""
  const res = await fetch(
    `${API_BASE}/corporations/me/accounting/profit${query}`,
    { credentials: "include" },
  )
  if (res.status === 402) return { access: false }
  if (!res.ok) throw new Error(`Couldn't load the numbers (error ${res.status}).`)
  return { access: true, profit: (await res.json()) as ProfitOut }
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

/** The recent hangar-check log (ADR-0044) — the "Needs a look" list's data. */
export const listReconciliationEvents = () =>
  apiGet<ReconciliationEventOut[]>("/corporations/me/accounting/reconciliation")

/** Run a hangar check right now instead of waiting for the hourly sync. */
export async function runHangarCheck(): Promise<HangarCheckResult> {
  const res = await apiSend("POST", "/corporations/me/accounting/hangar-check")
  if (!res.ok) await throwApiError(res, "The hangar check failed")
  return (await res.json()) as HangarCheckResult
}

/** The one corp wallet division buyback sales pay into (ADR-0045); null = the
 * sell side is off. */
export const getWalletDivision = () =>
  apiGet<{ division: number | null }>(
    "/corporations/me/accounting/wallet-division",
  )

export async function setWalletDivision(
  division: number | null,
): Promise<{ division: number | null }> {
  const res = await apiSend("PUT", "/corporations/me/accounting/wallet-division", {
    division,
  })
  if (!res.ok) await throwApiError(res, "Saving the wallet pick failed")
  return (await res.json()) as { division: number | null }
}

/** Manual entries (ADR-0045, #158): what ESI can't see, recorded by hand. */
export async function recordManualSale(
  payload: ManualSaleRequest,
): Promise<{ stock_was_missing: boolean }> {
  const res = await apiSend("POST", "/corporations/me/accounting/manual/sale", payload)
  if (!res.ok) await throwApiError(res, "Recording the sale failed")
  return (await res.json()) as { stock_was_missing: boolean }
}

export async function recordManualLot(payload: ManualLotRequest): Promise<void> {
  const res = await apiSend("POST", "/corporations/me/accounting/manual/lot", payload)
  if (!res.ok) await throwApiError(res, "Recording the stock failed")
}

export async function recordManualExpense(
  payload: ManualExpenseRequest,
): Promise<void> {
  const res = await apiSend(
    "POST",
    "/corporations/me/accounting/manual/expense",
    payload,
  )
  if (!res.ok) await throwApiError(res, "Recording the expense failed")
}

export const listReceivables = () =>
  apiGet<ReceivableOut[]>("/corporations/me/accounting/receivables")

export async function createReceivable(
  amount: string,
  note: string,
): Promise<ReceivableOut> {
  const res = await apiSend("POST", "/corporations/me/accounting/receivables", {
    amount,
    note,
  })
  if (!res.ok) await throwApiError(res, "Recording the owed ISK failed")
  return (await res.json()) as ReceivableOut
}

export async function clearReceivable(
  id: string,
  note?: string,
): Promise<ReceivableOut> {
  const res = await apiSend(
    "POST",
    `/corporations/me/accounting/receivables/${id}/clear`,
    { note: note ?? null },
  )
  if (!res.ok) await throwApiError(res, "Marking it paid failed")
  return (await res.json()) as ReceivableOut
}

/** The pre-filled reprocess form for a lot (ADR-0047): base-yield outputs where
 * the game data knows them — editable, because real yields vary. */
export const getReprocessPreview = (lotId: string) =>
  apiGet<ReprocessPreviewOut>(
    `/corporations/me/accounting/lots/${lotId}/reprocess-preview`,
  )

/** Record a reprocess: what we paid for the consumed units carries over into the
 * material stock entries. */
export async function recordReprocess(
  lotId: string,
  qty: number,
  outputs: { type_id: number; quantity: number }[],
): Promise<ReprocessResultOut> {
  const res = await apiSend(
    "POST",
    `/corporations/me/accounting/lots/${lotId}/reprocess`,
    { qty, outputs },
  )
  if (!res.ok) await throwApiError(res, "Recording the reprocess failed")
  return (await res.json()) as ReprocessResultOut
}
