"""Pydantic read-models returned by the data layer.

Database logic never hands ORM entities to the rest of the app — it returns
these immutable records. The interface layer maps them to API DTOs (schemas/),
so the database shape never leaks into the public API contract.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.entitlements import EntitlementSource, Feature
from app.domain.locations import LocationKind
from app.domain.market import HubKind
from app.domain.pricing import AggregateField, Basis, LineStatus, TargetKind


class CharacterRecord(BaseModel):
    # `character_id` keeps its API name but sources from the `eve_id` column (ADR-0025);
    # `id` is the internal UUID used for FK relationships.
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    character_id: int = Field(validation_alias="eve_id")
    name: str
    last_login_at: datetime


class CorporationRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    corporation_id: int = Field(validation_alias="eve_id")
    name: str
    ceo_character_id: int
    registered_by_character_id: int
    registered_at: datetime


class ManagerRecord(BaseModel):
    """A manager assignment joined with the character's name."""

    character_id: int
    character_name: str
    granted_by_character_id: int
    granted_at: datetime


class CorpMemberRecord(BaseModel):
    """A corp roster member (EVE character id + name) for the manager-designation
    picker (ADR-0036)."""

    character_id: int
    name: str


class SdeTypeRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    type_id: int
    name: str
    group_id: int
    category_id: int | None
    market_group_id: int | None
    volume: Decimal
    portion_size: int
    published: bool


class SdeMarketGroupRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    market_group_id: int
    parent_id: int | None
    name: str


class MarketPriceRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    hub_id: str
    type_id: int

    buy_weighted_average: Decimal
    buy_max: Decimal
    buy_min: Decimal
    buy_median: Decimal
    buy_percentile: Decimal
    buy_volume: Decimal
    buy_order_count: int

    sell_weighted_average: Decimal
    sell_max: Decimal
    sell_min: Decimal
    sell_median: Decimal
    sell_percentile: Decimal
    sell_volume: Decimal
    sell_order_count: int

    fetched_at: datetime


class ConfiguredHubRecord(BaseModel):
    """A market hub referenced by a corp's config or a pricing rule (ADR-0034), with
    the owning corp — enough to choose a price source and, for structures, a token.
    Built from selected columns (not a single ORM row), so no `from_attributes`."""

    hub_id: str
    kind: HubKind
    region_id: int | None = None
    corporation_id: uuid.UUID


class SdeMetadataRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source: str
    type_count: int
    market_group_count: int
    imported_at: datetime


class SdeStationRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    station_id: int
    name: str
    system_name: str
    region_id: int


class BuybackConfigRecord(BaseModel):
    # No corporation_id: a config belongs to the caller's corp implicitly; the
    # interface sets the API's EVE corp id from the session.
    model_config = ConfigDict(from_attributes=True)

    market_hub_id: str
    # Hub source descriptor (ADR-0028): kind + ESI region + cached display name.
    market_hub_kind: HubKind = "npc_station"
    market_region_id: int | None = None
    market_hub_name: str | None = None
    default_basis: Basis
    default_percentage: Decimal
    aggregate_field: AggregateField
    # Global-default accept flag (ADR-0007): False → whitelist-only buyback.
    default_accepted: bool = True


class PricingRuleRecord(BaseModel):
    # A rule is identified by its target (ADR-0022); neither the corp FK nor the UUID
    # PK is surfaced.
    model_config = ConfigDict(from_attributes=True)

    target_kind: TargetKind
    target_id: int
    # Human-readable name of the target (SDE type or market-group name), resolved by
    # the application layer for display. None if the target no longer exists in the SDE.
    target_name: str | None = None
    # The market group the target belongs to (a type's own group, or the target group
    # itself for a market-group rule), so the UI can file the rule under a category
    # folder. Resolved by the application layer; None if unknown/removed from the SDE.
    target_market_group_id: int | None = None
    basis: Basis | None
    percentage: Decimal
    enabled: bool
    # Price a matched ore by its refined mineral value (ADR-0026); ignored for non-ores.
    reprocess: bool = False
    # Accept only the compressed variants of matched ores (ADR-0026); ore-only.
    compressed_only: bool = False
    # False → the buyback rejects matching items (a blacklist rule).
    accepted: bool = True
    # Manager-assigned folder for organising rules (ADR-0039); null → category folder.
    folder: str | None = None
    # Per-rule market-hub override (ADR-0031); all None → corp default hub.
    market_hub_id: str | None = None
    market_hub_kind: HubKind | None = None
    market_region_id: int | None = None
    market_hub_name: str | None = None


class AppraisalLineRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    type_id: int | None
    type_name: str
    quantity: int
    status: LineStatus
    basis: Basis | None
    percentage: Decimal | None
    unit_value: Decimal | None
    unit_price: Decimal | None
    line_total: Decimal
    reason: str | None
    # Reprocessed-mineral breakdown snapshot (ADR-0026); None for direct/rejected lines.
    reprocess: dict | None = None
    # Hub override snapshot (ADR-0031); None → priced at the appraisal's default hub.
    market_hub_id: str | None = None
    market_hub_name: str | None = None


class BuybackLocationRecord(BaseModel):
    """An accepted drop-off location (ADR-0030). `location_id` is the EVE station or
    structure id as a string; `system_name` is set for NPC stations only."""

    model_config = ConfigDict(from_attributes=True)

    location_id: str
    kind: LocationKind
    name: str
    system_name: str | None = None


class AppraisalRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    corporation_id: uuid.UUID  # internal corp UUID, for the corp-scope check only
    created_by_character_id: int
    created_by_character_name: str | None = None
    created_at: datetime
    market_hub_id: str
    accepted_total: Decimal
    rejected_count: int
    # Drop-off location snapshot (ADR-0030); null for pre-feature appraisals.
    delivery_location_id: str | None = None
    delivery_location_name: str | None = None
    lines: list[AppraisalLineRecord]
    # The current matched-contract status (ADR-0037), resolved by a LEFT JOIN on
    # `appraisal_contracts`; None when no contract is matched.
    contract_status: str | None = None


class AppraisalPreviewRecord(BaseModel):
    """The minimal, public (unauthenticated) view of an appraisal behind a shared link,
    for the link-unfurl preview (ADR-0040): total value + drop-off location only. No
    character, items, or corp — the public_id is the capability."""

    model_config = ConfigDict(from_attributes=True)

    accepted_total: Decimal
    delivery_location_name: str | None = None


class AppraisalSummaryRecord(BaseModel):
    """An appraisal header without its lines, for list views."""

    model_config = ConfigDict(from_attributes=True)

    public_id: str
    created_by_character_id: int
    created_by_character_name: str | None = None
    created_at: datetime
    market_hub_id: str
    accepted_total: Decimal
    rejected_count: int
    delivery_location_id: str | None = None
    delivery_location_name: str | None = None
    # Matched-contract status (ADR-0037); None when no contract is matched.
    contract_status: str | None = None


class AppraisalContractRecord(BaseModel):
    """The current best contract matched to an appraisal (ADR-0037)."""

    model_config = ConfigDict(from_attributes=True)

    appraisal_id: uuid.UUID
    contract_id: int
    status: str
    issued_at: datetime
    completed_at: datetime | None = None


class EntitlementRecord(BaseModel):
    """A corp's access to a paid feature (ADR-0042). `expires_at` NULL = perpetual;
    activeness is decided by `domain.entitlements.entitlement_active`, not here."""

    model_config = ConfigDict(from_attributes=True)

    corporation_id: uuid.UUID
    feature: Feature
    source: EntitlementSource
    granted_at: datetime
    expires_at: datetime | None = None
    granted_by_character_id: int | None = None


class CorpFeatureAccessRecord(BaseModel):
    """One corp's entitlement facts for a feature (ADR-0042), for the app-admin list —
    a deliberate cross-tenant read (ADR-0041). Entitlement fields are None when the corp
    holds no row; "active" is computed by the application layer, not stored here."""

    corporation_id: int  # EVE id (the admin API is EVE-keyed, ADR-0025)
    corporation_name: str
    source: EntitlementSource | None = None
    granted_at: datetime | None = None
    expires_at: datetime | None = None
    granted_by_character_id: int | None = None


class CorpEsiTokenRecord(BaseModel):
    """A corp's structure-market authorization (ADR-0029). Carries the ciphertext for
    internal refresh use; the API schema (`StructureTokenStatus`) omits it."""

    model_config = ConfigDict(from_attributes=True)

    corporation_id: uuid.UUID
    character_eve_id: int
    character_name: str
    encrypted_refresh_token: bytes
    scopes: str
    created_at: datetime
    last_refresh_failed_at: datetime | None = None
    last_used_at: datetime | None = None


class OperatorWalletTokenRecord(BaseModel):
    """The operator's wallet authorization (ADR-0042). Carries the ciphertext for
    internal refresh use; the admin API DTO omits it."""

    model_config = ConfigDict(from_attributes=True)

    character_eve_id: int
    character_name: str
    encrypted_refresh_token: bytes
    scopes: str
    created_at: datetime
    last_refresh_failed_at: datetime | None = None


class WalletPaymentRecord(BaseModel):
    """One incoming ISK transfer from the operator's wallet journal (ADR-0042).
    `matched_corporation_id`/`matched_corporation_name` are set once the payment has
    extended a corp's access (name join-derived; None if never matched)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    journal_id: int
    amount: Decimal
    sender_eve_id: int | None = None
    sender_name: str | None = None
    reason: str | None = None
    received_at: datetime
    matched_corporation_id: uuid.UUID | None = None
    matched_corporation_name: str | None = None
    periods_granted: int = 0
    matched_at: datetime | None = None
    matched_by_character_id: int | None = None
