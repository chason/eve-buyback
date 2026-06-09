from pydantic import BaseModel, ConfigDict


class TypeSearchResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    type_id: int
    name: str
    market_group_id: int | None


class MarketGroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    market_group_id: int
    parent_id: int | None
    name: str


class StationSearchResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    station_id: int
    name: str
    system_name: str
    region_id: int
