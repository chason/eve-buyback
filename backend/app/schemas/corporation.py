from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CorporationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    corporation_id: int
    name: str
    ceo_character_id: int
    registered_by_character_id: int
    registered_at: datetime


class ManagerCreateRequest(BaseModel):
    character_id: int


class ManagerOut(BaseModel):
    character_id: int
    character_name: str
    granted_by_character_id: int
    granted_at: datetime
