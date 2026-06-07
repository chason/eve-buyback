from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.pricing import AggregateField, Basis, TargetKind


class ConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    corporation_id: int
    market_hub_id: int
    default_basis: Basis
    default_percentage: Decimal
    aggregate_field: AggregateField


class ConfigUpdateRequest(BaseModel):
    market_hub_id: int
    default_basis: Basis
    default_percentage: Decimal = Field(ge=0)
    aggregate_field: AggregateField


class RuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    target_kind: TargetKind
    target_id: int
    basis: Basis | None
    percentage: Decimal
    enabled: bool


class RuleCreateRequest(BaseModel):
    target_kind: TargetKind
    target_id: int
    basis: Basis | None = None
    percentage: Decimal = Field(ge=0)
    enabled: bool = True


class RuleUpdateRequest(BaseModel):
    """PATCH: only the fields sent are changed (router uses `exclude_unset`).
    `target_kind`/`target_id` are immutable — delete and recreate to retarget."""

    basis: Basis | None = None
    percentage: Decimal | None = Field(default=None, ge=0)
    enabled: bool | None = None
