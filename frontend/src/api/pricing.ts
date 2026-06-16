import { apiGet, apiSend, throwApiError } from "./client"
import type {
  ConfigOut,
  ConfigUpdateRequest,
  RuleOut,
  RulePutRequest,
  TargetKind,
} from "./types"

export type {
  Basis,
  AggregateField,
  ConfigOut,
  ConfigUpdateRequest,
  RuleOut,
  RulePutRequest,
  TargetKind,
} from "./types"

export const getConfig = () => apiGet<ConfigOut>("/corporations/me/config")

export async function updateConfig(
  body: ConfigUpdateRequest,
): Promise<ConfigOut> {
  const res = await apiSend("PUT", "/corporations/me/config", body)
  if (!res.ok) await throwApiError(res, "Save config failed")
  return (await res.json()) as ConfigOut
}

export const listRules = () => apiGet<RuleOut[]>("/corporations/me/rules")

export async function putRule(
  targetKind: TargetKind,
  targetId: number,
  body: RulePutRequest,
): Promise<RuleOut> {
  const res = await apiSend(
    "PUT",
    `/corporations/me/rules/${targetKind}/${targetId}`,
    body,
  )
  if (!res.ok) await throwApiError(res, "Save rule failed")
  return (await res.json()) as RuleOut
}

export async function deleteRule(
  targetKind: TargetKind,
  targetId: number,
): Promise<void> {
  const res = await apiSend(
    "DELETE",
    `/corporations/me/rules/${targetKind}/${targetId}`,
  )
  if (!res.ok) await throwApiError(res, "Delete rule failed")
}
