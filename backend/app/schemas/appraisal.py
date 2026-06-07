from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.pricing import Basis, LineStatus


class AppraisalItemIn(BaseModel):
    type_id: int
    quantity: int = Field(ge=1)


class AppraisalCreateRequest(BaseModel):
    """Items may be supplied structured, as a raw EVE paste, or both — but at least
    one must be non-empty. The paste is parsed and name-resolved server-side."""

    items: list[AppraisalItemIn] = Field(default_factory=list)
    paste: str | None = None

    @model_validator(mode="after")
    def _require_some_input(self) -> "AppraisalCreateRequest":
        if not self.items and not (self.paste and self.paste.strip()):
            raise ValueError("Provide at least one item or a non-empty paste")
        return self


class AppraisalLineOut(BaseModel):
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
