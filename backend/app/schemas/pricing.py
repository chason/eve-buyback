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

    # A rule is identified by its target; no surrogate id is exposed (ADR-0022).
    target_kind: TargetKind
    target_id: int
    # The target's SDE name (type or market-group), for display; None if the target
    # is no longer in the SDE.
    target_name: str | None = None
    basis: Basis | None
    percentage: Decimal
    enabled: bool
    # Price a matched ore by refined mineral value (ADR-0026); ignored for non-ores.
    reprocess: bool = False
    # Accept only the compressed variants of matched ores (ADR-0026); ore-only.
    compressed_only: bool = False


class RulePutRequest(BaseModel):
    """The full rule state for a target (the target comes from the URL). `PUT` is an
    idempotent create-or-replace, so this is the complete representation."""

    basis: Basis | None = None
    percentage: Decimal = Field(ge=0)
    enabled: bool = True
    reprocess: bool = False
    compressed_only: bool = False
