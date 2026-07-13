"""Accounting add-on API DTOs (ADR-0043). The inventory view (#152): what the corp's
buyback owns now, carried at cost, with verified and estimated cost kept apart."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class InventoryLotOut(BaseModel):
    """One open purchase of the item: what's left of it, what one unit is carried
    at, and how long it has been sitting."""

    qty: int
    unit_cost: Decimal
    total_cost: Decimal
    acquired_at: datetime
    days_held: int
    stale: bool
    cost_is_estimated: bool


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
