import { apiGet } from "./client"
import type { AccountingAccessOut } from "./types"

export type { AccountingAccessOut } from "./types"

/** The corp's accounting-access status + how to pay (managers, ADR-0042). */
export const getAccountingAccess = () =>
  apiGet<AccountingAccessOut>("/corporations/me/accounting-access")
