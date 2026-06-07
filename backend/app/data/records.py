"""Pydantic read-models returned by the data layer.

Database logic never hands ORM entities to the rest of the app — it returns
these immutable records. The interface layer maps them to API DTOs (schemas/),
so the database shape never leaks into the public API contract.
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class CharacterRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    character_id: int
    name: str
    last_login_at: datetime


class CorporationRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    corporation_id: int
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
    market_group_id: int | None
    volume: Decimal
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
