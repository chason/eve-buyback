"""Pydantic read-models returned by the data layer.

Database logic never hands ORM entities to the rest of the app — it returns
these immutable records. The interface layer maps them to API DTOs (schemas/),
so the database shape never leaks into the public API contract.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

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

    hub_id: int
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


class SdeMetadataRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source: str
    type_count: int
    market_group_count: int
    imported_at: datetime


class BuybackConfigRecord(BaseModel):
    # No corporation_id: a config belongs to the caller's corp implicitly; the
    # interface sets the API's EVE corp id from the session.
    model_config = ConfigDict(from_attributes=True)

    market_hub_id: int
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
    basis: Basis | None
    percentage: Decimal
    enabled: bool
    # Price a matched ore by its refined mineral value (ADR-0026); ignored for non-ores.
    reprocess: bool = False
    # Accept only the compressed variants of matched ores (ADR-0026); ore-only.
    compressed_only: bool = False
    # False → the buyback rejects matching items (a blacklist rule).
    accepted: bool = True


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


class AppraisalRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    corporation_id: uuid.UUID  # internal corp UUID, for the corp-scope check only
    created_by_character_id: int
    created_by_character_name: str | None = None
    created_at: datetime
    market_hub_id: int
    accepted_total: Decimal
    rejected_count: int
    lines: list[AppraisalLineRecord]


class AppraisalSummaryRecord(BaseModel):
    """An appraisal header without its lines, for list views."""

    model_config = ConfigDict(from_attributes=True)

    public_id: str
    created_by_character_id: int
    created_by_character_name: str | None = None
    created_at: datetime
    market_hub_id: int
    accepted_total: Decimal
    rejected_count: int
