import { apiGet, apiSend, throwApiError } from "./client"
import type {
  AppraisalCreateRequest,
  AppraisalOut,
  AppraisalSummaryOut,
} from "./types"

export type {
  AppraisalCreateRequest,
  AppraisalLineOut,
  AppraisalOut,
  AppraisalSummaryOut,
} from "./types"

export async function createAppraisal(
  body: AppraisalCreateRequest,
): Promise<AppraisalOut> {
  const res = await apiSend("POST", "/appraisals", body)
  if (!res.ok) await throwApiError(res, "Appraisal failed")
  return (await res.json()) as AppraisalOut
}

export const getAppraisal = (publicId: string) =>
  apiGet<AppraisalOut>(`/appraisals/${encodeURIComponent(publicId)}`)

/** List appraisals: own for members, the whole corp's for managers/CEO. */
export const listAppraisals = () =>
  apiGet<AppraisalSummaryOut[]>("/appraisals")

/** Open the appraisal's matched contract in the caller's own EVE client (ADR-0038).
 * Succeeds silently (204); surfaces the backend's "log in again" detail on failure. */
export async function openContract(publicId: string): Promise<void> {
  const res = await apiSend(
    "POST",
    `/appraisals/${encodeURIComponent(publicId)}/open-contract`,
  )
  if (!res.ok) await throwApiError(res, "Couldn't open the contract in EVE")
}
