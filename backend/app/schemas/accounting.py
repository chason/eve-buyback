"""Accounting add-on API DTOs (ADR-0043). The inventory view (#152): what the corp's
buyback owns now, carried at cost, with verified and estimated cost kept apart."""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class InventoryLotOut(BaseModel):
    """One open purchase of the item: what's left of it, what one unit is carried
    at, and how long it has been sitting. `id` keys the reprocess action (#177)."""

    id: uuid.UUID
    qty: int
    unit_cost: Decimal
    total_cost: Decimal
    acquired_at: datetime
    days_held: int
    stale: bool
    cost_is_estimated: bool
    # Provenance (#158): buyback | opening_balance | manual | reprocess.
    source: str


class InventoryItemOut(BaseModel):
    type_id: int
    type_name: str | None = None
    qty: int
    total_cost: Decimal
    oldest_days: int
    stale: bool
    any_estimated: bool
    # What the holding would fetch today and the paper gain/loss vs cost (#153);
    # None when the type has no cached market price.
    worth: Decimal | None = None
    unrealized: Decimal | None = None
    lots: list[InventoryLotOut]


class HangarOut(BaseModel):
    """One configured buyback hangar (ADR-0044): a drop-off location + a corp hangar
    division whose contents count as buyback stock."""

    location_id: str
    location_name: str
    division: int


class HangarCreateRequest(BaseModel):
    # Must be one of the corp's drop-off locations (ADR-0030); 404 otherwise.
    location_id: str
    division: int = Field(ge=1, le=7)


class WalletDivisionOut(BaseModel):
    """The corp wallet division buyback sales pay into (ADR-0045). None = sell-side
    ingestion off."""

    division: int | None = None


class WalletDivisionUpdateRequest(BaseModel):
    division: int | None = Field(default=None, ge=1, le=7)


class ManualSaleRequest(BaseModel):
    """An off-game sale a manager records by hand (ADR-0045, #158). The note is
    required — provenance without a why is half a record."""

    type_id: int
    qty: int = Field(gt=0)
    unit_proceeds: Decimal = Field(ge=0)
    location_id: str
    note: str = Field(min_length=3)
    sold_at: datetime | None = None


class ManualSaleResult(BaseModel):
    # True when the books didn't have (all of) that stock — the deemed-cost
    # fallback fired and a flagged entry landed in the hangar-check log.
    stock_was_missing: bool


class ManualLotRequest(BaseModel):
    """Off-app stock with a known (or best-guess) cost. `cost_is_estimated` is the
    manager's honesty switch: False = the price is known, True = a guess."""

    type_id: int
    qty: int = Field(gt=0)
    unit_cost: Decimal = Field(ge=0)
    location_id: str
    note: str = Field(min_length=3)
    cost_is_estimated: bool = False
    acquired_at: datetime | None = None


class ManualExpenseRequest(BaseModel):
    """A cost ESI didn't book. A NEGATIVE amount is a correction — a reversing
    entry whose note says what it offsets (never an edit or delete)."""

    kind: str = Field(pattern="^(broker_fee|relist_fee|hauling|other)$")
    amount: Decimal
    note: str = Field(min_length=3)
    incurred_at: datetime | None = None


class ReceivableCreateRequest(BaseModel):
    amount: Decimal = Field(gt=0)
    note: str = Field(min_length=3)


class ReceivableClearRequest(BaseModel):
    note: str | None = None


class ReceivableOut(BaseModel):
    id: uuid.UUID
    amount: Decimal
    note: str
    incurred_at: datetime
    cleared_at: datetime | None = None
    cleared_note: str | None = None


class ReconciliationEventOut(BaseModel):
    """One "Needs a look" / hangar-check log entry (ADR-0044). `kind` is excess
    (stock found beyond the books — normally booked as an estimated-value lot) or
    shortfall (books expect more than the hangar holds — always a human's call)."""

    kind: str
    type_id: int
    type_name: str | None = None
    location_id: str
    location_name: str | None = None
    qty: int
    unit_cost: Decimal | None = None
    # True when an excess was booked as a lot; False = couldn't be priced yet.
    booked: bool
    flagged: bool
    note: str | None = None
    occurred_at: datetime


class HangarCheckResult(BaseModel):
    lots_added: int
    flagged: int


class ReprocessOutputIn(BaseModel):
    type_id: int
    quantity: int = Field(gt=0)


class ReprocessRequest(BaseModel):
    """Record a reprocess (ADR-0047): how many units left the lot, and what
    actually came out (editable — real yields vary with skills/structures)."""

    qty: int = Field(gt=0)
    outputs: list[ReprocessOutputIn] = Field(min_length=1)


class ReprocessOutputOut(BaseModel):
    type_id: int
    type_name: str | None = None
    quantity: int


class ReprocessPreviewOut(BaseModel):
    """The pre-filled record form: the source lot's facts + base-yield outputs
    (empty when the type has no yield data — outputs are then hand-entered)."""

    lot_id: uuid.UUID
    type_id: int
    type_name: str | None = None
    qty_remaining: int
    outputs: list[ReprocessOutputOut]


class ReprocessResultOut(BaseModel):
    """What the record created, in plain terms: one child stock entry per material,
    carrying the source's cost."""

    children: list[ReprocessOutputOut]


class ChannelProfitOut(BaseModel):
    """One sales channel's slice of the period: market | contract | direct."""

    channel: str
    revenue: Decimal
    sales_tax: Decimal
    cost_of_goods: Decimal
    margin: Decimal
    sale_count: int


class ProfitOut(BaseModel):
    """The "How we're doing" period fold (#159). `margin` = revenue − tax − cost
    of goods; `profit` subtracts the period's expenses on top. `measured_margin` +
    `estimated_margin` == `margin` — deemed-cost results stay visibly apart."""

    since: datetime | None = None
    until: datetime | None = None
    revenue: Decimal
    sales_tax: Decimal
    cost_of_goods: Decimal
    margin: Decimal
    measured_margin: Decimal
    estimated_margin: Decimal
    fees: Decimal
    write_downs: Decimal
    other_expenses: Decimal
    profit: Decimal
    sale_count: int
    channels: list[ChannelProfitOut]


class InventoryOut(BaseModel):
    total_cost: Decimal
    verified_cost: Decimal
    estimated_cost: Decimal
    # Lots held at least this many days are flagged `stale` (config, #152).
    stale_days: int
    # "If we sold it all today" + the paper gain/loss as its own line (#153), over
    # the types the market cache can price; `unpriced_types` counts the rest.
    worth_total: Decimal
    unrealized_total: Decimal
    unpriced_types: int
    items: list[InventoryItemOut]
