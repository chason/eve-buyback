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

// Corp ESI access token also powers the manager-designation roster (ADR-0036).
export type RosterStatusOut = Schemas["RosterStatusOut"]
export type CorpMemberOut = Schemas["CorpMemberOut"]
export type ManagerOut = Schemas["ManagerOut"]
export type ManagerCreateRequest = Schemas["ManagerCreateRequest"]

// Accepted buyback drop-off locations (ADR-0030).
export type LocationOut = Schemas["LocationOut"]
export type LocationCreateRequest = Schemas["LocationCreateRequest"]
export type LocationKind = LocationOut["kind"]

// App-admin access management (ADR-0041/0042).
export type CorpAccessOut = Schemas["CorpAccessOut"]
export type AccessGrantRequest = Schemas["AccessGrantRequest"]
export type OperatorWalletStatus = Schemas["OperatorWalletStatus"]
export type PaymentOut = Schemas["PaymentOut"]
export type AccountingAccessOut = Schemas["AccountingAccessOut"]
export type BillingSettingsOut = Schemas["BillingSettingsOut"]

// Accounting add-on: the "What we've got" inventory view (ADR-0043, #152).
export type InventoryOut = Schemas["InventoryOut"]
export type InventoryItemOut = Schemas["InventoryItemOut"]
export type InventoryLotOut = Schemas["InventoryLotOut"]
// Buyback-hangar config for the hangar check (ADR-0044, #154).
export type HangarOut = Schemas["HangarOut"]
export type HangarCreateRequest = Schemas["HangarCreateRequest"]
// Hangar reconciliation (ADR-0044, #155): the "Needs a look" log + manual check.
export type ReconciliationEventOut = Schemas["ReconciliationEventOut"]
export type HangarCheckResult = Schemas["HangarCheckResult"]
// Reprocess transformations (ADR-0047, #177).
export type ReprocessPreviewOut = Schemas["ReprocessPreviewOut"]
export type ReprocessResultOut = Schemas["ReprocessResultOut"]

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
