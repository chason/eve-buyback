import { apiGet, apiSend } from "./client"
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
  if (!res.ok) throw new Error(`Save config failed: ${res.status}`)
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
  if (!res.ok) throw new Error(`Save rule failed: ${res.status}`)
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
  if (!res.ok) throw new Error(`Delete rule failed: ${res.status}`)
}
