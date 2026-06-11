// Friendly aliases over the generated OpenAPI schema (ADR-0011). Types are derived
// from the backend contract, never hand-authored. Regenerate with `npm run gen:api`.
import type { components } from "./schema"

type Schemas = components["schemas"]

export type SessionUser = Schemas["SessionUser"]
export type Role = SessionUser["role"]
export type CorporationOut = Schemas["CorporationOut"]
export type LoginUrlResponse = Schemas["LoginUrlResponse"]

export type AppraisalOut = Schemas["AppraisalOut"]
export type AppraisalLineOut = Schemas["AppraisalLineOut"]
export type AppraisalSummaryOut = Schemas["AppraisalSummaryOut"]
export type AppraisalCreateRequest = Schemas["AppraisalCreateRequest"]
export type AppraisalItemIn = Schemas["AppraisalItemIn"]

export type TypeSearchResult = Schemas["TypeSearchResult"]
export type MarketGroupOut = Schemas["MarketGroupOut"]
export type StationSearchResult = Schemas["StationSearchResult"]
export type StructureTokenStatus = Schemas["StructureTokenStatus"]
export type StructureAuthorizeResponse = Schemas["StructureAuthorizeResponse"]
export type StructureSearchResult = Schemas["StructureSearchResult"]

// Accepted buyback drop-off locations (ADR-0030).
export type LocationOut = Schemas["LocationOut"]
export type LocationCreateRequest = Schemas["LocationCreateRequest"]
export type LocationKind = LocationOut["kind"]

// Used by the M6b rule editor / config view.
export type ConfigOut = Schemas["ConfigOut"]
export type ConfigUpdateRequest = Schemas["ConfigUpdateRequest"]
export type RuleOut = Schemas["RuleOut"]
export type RulePutRequest = Schemas["RulePutRequest"]

// Closed-set enums shared by config + rules (derived from the contract).
export type Basis = ConfigOut["default_basis"]
export type AggregateField = ConfigOut["aggregate_field"]
export type TargetKind = RuleOut["target_kind"]
export type HubKind = ConfigOut["market_hub_kind"]
