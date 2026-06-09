from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.market import HubKind
from app.domain.pricing import AggregateField, Basis, TargetKind


class ConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    corporation_id: int
    market_hub_id: int
    # Hub source (ADR-0028): kind + cached ESI region + display name (region/name are
    # null for the Fuzzwork hubs, which need no resolution).
    market_hub_kind: HubKind = "npc_station"
    market_region_id: int | None = None
    market_hub_name: str | None = None
    default_basis: Basis
    default_percentage: Decimal
    aggregate_field: AggregateField
    default_accepted: bool = True


class ConfigUpdateRequest(BaseModel):
    market_hub_id: int
    # Defaults to an NPC station; structures arrive in a later phase. region_id/name
    # are resolved server-side from the id, so they aren't part of the request.
    market_hub_kind: HubKind = "npc_station"
    default_basis: Basis
    default_percentage: Decimal = Field(ge=0)
    aggregate_field: AggregateField
    default_accepted: bool = True


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
    # False → the buyback rejects matching items (a blacklist rule).
    accepted: bool = True


class RulePutRequest(BaseModel):
    """The full rule state for a target (the target comes from the URL). `PUT` is an
    idempotent create-or-replace, so this is the complete representation."""

    basis: Basis | None = None
    percentage: Decimal = Field(ge=0)
    enabled: bool = True
    reprocess: bool = False
    compressed_only: bool = False
    accepted: bool = True
