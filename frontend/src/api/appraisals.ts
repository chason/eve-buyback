import { apiGet, apiSend } from "./client"
import type { AppraisalCreateRequest, AppraisalOut } from "./types"

export type { AppraisalCreateRequest, AppraisalLineOut, AppraisalOut } from "./types"

export async function createAppraisal(
  body: AppraisalCreateRequest,
): Promise<AppraisalOut> {
  const res = await apiSend("POST", "/appraisals", body)
  if (!res.ok) throw new Error(`Appraisal failed: ${res.status}`)
  return (await res.json()) as AppraisalOut
}

export const getAppraisal = (publicId: string) =>
  apiGet<AppraisalOut>(`/appraisals/${encodeURIComponent(publicId)}`)
