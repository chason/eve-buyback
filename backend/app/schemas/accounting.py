"""Accounting add-on API DTOs (ADR-0043). The inventory view (#152): what the corp's
buyback owns now, carried at cost, with verified and estimated cost kept apart."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


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
    lots: list[InventoryLotOut]


class InventoryOut(BaseModel):
    total_cost: Decimal
    verified_cost: Decimal
    estimated_cost: Decimal
    # Lots held at least this many days are flagged `stale` (config, #152).
    stale_days: int
    items: list[InventoryItemOut]
