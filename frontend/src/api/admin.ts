import { apiGet, apiSend, throwApiError } from "./client"
import type {
  BillingSettingsOut,
  CorpAccessOut,
  OperatorWalletStatus,
  PaymentOut,
} from "./types"

export type {
  BillingSettingsOut,
  CorpAccessOut,
  OperatorWalletStatus,
  PaymentOut,
} from "./types"

// The operator-wallet SSO flow's state prefix (must match the backend's
// application/operator_wallet.py) — the shared callback routes on it.
export const WALLET_STATE_PREFIX = "opwallet."

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

/** The access price the instance charges (runtime-editable, ADR-0042). */
export const getBillingSettings = () =>
  apiGet<BillingSettingsOut>("/admin/billing")

/** Set the access price (applies to checkout and all future payment matching). */
export async function updateBillingSettings(
  priceIsk: number,
): Promise<BillingSettingsOut> {
  const res = await apiSend("PUT", "/admin/billing", { price_isk: priceIsk })
  if (!res.ok) await throwApiError(res, "Saving the price failed")
  return (await res.json()) as BillingSettingsOut
}

/** The operator wallet connection (payment reconciliation, ADR-0042). */
export const getWalletStatus = () =>
  apiGet<OperatorWalletStatus>("/admin/wallet")

/** Begin the operator-wallet SSO grant; returns the EVE authorize URL. */
export async function beginWalletAuthorize(): Promise<string> {
  const res = await apiSend("POST", "/admin/wallet/authorize")
  if (!res.ok) await throwApiError(res, "Starting the wallet connection failed")
  return ((await res.json()) as { authorization_url: string }).authorization_url
}

/** Complete the operator-wallet SSO grant (called from the shared callback). */
export async function completeWalletAuthorize(
  code: string,
  state: string,
): Promise<OperatorWalletStatus> {
  const res = await apiSend("POST", "/admin/wallet/session", { code, state })
  if (!res.ok) await throwApiError(res, "Connecting the wallet failed")
  return (await res.json()) as OperatorWalletStatus
}

/** Disconnect the operator wallet. */
export async function revokeWallet(): Promise<void> {
  const res = await apiSend("DELETE", "/admin/wallet")
  if (!res.ok) await throwApiError(res, "Disconnecting the wallet failed")
}

/** Recent incoming ISK payments (newest first). */
export const listPayments = (unmatchedOnly = false) =>
  apiGet<PaymentOut[]>(`/admin/payments${unmatchedOnly ? "?unmatched=true" : ""}`)

/** Apply an unmatched payment to a corporation. */
export async function matchPayment(
  paymentId: string,
  corporationId: number,
): Promise<PaymentOut> {
  const res = await apiSend("POST", `/admin/payments/${paymentId}/match`, {
    corporation_id: corporationId,
  })
  if (!res.ok) await throwApiError(res, "Applying the payment failed")
  return (await res.json()) as PaymentOut
}
