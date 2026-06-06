"""Pydantic read-models returned by the data layer.

Database logic never hands ORM entities to the rest of the app — it returns
these immutable records. The interface layer maps them to API DTOs (schemas/),
so the database shape never leaks into the public API contract.
"""

from datetime import datetime

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
