from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.pricing import Basis, LineStatus


class AppraisalItemIn(BaseModel):
    type_id: int
    quantity: int = Field(ge=1)


class AppraisalCreateRequest(BaseModel):
    items: list[AppraisalItemIn] = Field(min_length=1)


class AppraisalLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    type_id: int
    type_name: str
    quantity: int
    status: LineStatus
    basis: Basis | None
    percentage: Decimal | None
    unit_value: Decimal | None
    unit_price: Decimal | None
    line_total: Decimal
    reason: str | None


class AppraisalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    created_by_character_id: int
    created_at: datetime
    market_hub_id: int
    accepted_total: Decimal
    rejected_count: int
    lines: list[AppraisalLineOut]


class AppraisalSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    created_by_character_id: int
    created_at: datetime
    market_hub_id: int
    accepted_total: Decimal
    rejected_count: int
