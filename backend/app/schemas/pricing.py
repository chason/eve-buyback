from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.market import HubKind
from app.domain.pricing import AggregateField, Basis, TargetKind

# A buyback percentage is a share of market value, so even a generous "above
# market" rule stays well under this. The cap just rejects absurd or fat-fingered
# input (e.g. a stray extra digit); the exact ceiling isn't otherwise meaningful (#28).
MAX_PERCENTAGE = Decimal(1000)


class ConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    corporation_id: int
    market_hub_id: str
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
    market_hub_id: str
    # Defaults to an NPC station. For NPC/Fuzzwork hubs the region_id/name are
    # resolved server-side from the id. For a **structure** there's no SDE to resolve
    # against, so the client passes the friendly name it got from the structure
    # search (ADR-0029); ignored for the other kinds.
    market_hub_kind: HubKind = "npc_station"
    market_hub_name: str | None = None
    default_basis: Basis
    default_percentage: Decimal = Field(ge=0, le=MAX_PERCENTAGE)
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
    # The market group the target belongs to, so the UI can file the rule under its
    # category folder (#rules-folders). None if unknown/removed from the SDE.
    target_market_group_id: int | None = None
    basis: Basis | None
    percentage: Decimal
    enabled: bool
    # Price a matched ore by refined mineral value (ADR-0026); ignored for non-ores.
    reprocess: bool = False
    # Accept only the compressed variants of matched ores (ADR-0026); ore-only.
    compressed_only: bool = False
    # False → the buyback rejects matching items (a blacklist rule).
    accepted: bool = True
    # Manager-assigned folder for organising rules (ADR-0039); null → category folder.
    folder: str | None = None
    # Per-rule market-hub override (ADR-0031); all null → the corp's default hub.
    market_hub_id: str | None = None
    market_hub_kind: HubKind | None = None
    market_hub_name: str | None = None


class RulePutRequest(BaseModel):
    """The full rule state for a target (the target comes from the URL). `PUT` is an
    idempotent create-or-replace, so this is the complete representation — omitting
    the hub fields clears any hub override (ADR-0031)."""

    basis: Basis | None = None
    percentage: Decimal = Field(ge=0, le=MAX_PERCENTAGE)
    enabled: bool = True
    reprocess: bool = False
    compressed_only: bool = False
    accepted: bool = True
    # Optional folder label for organising rules (ADR-0039); blank/whitespace is
    # normalised to null (file by market category) in the use case.
    folder: str | None = Field(default=None, max_length=64)
    # Hub override: id + kind, plus the friendly name for structures (no SDE to
    # resolve against — same posture as ConfigUpdateRequest).
    market_hub_id: str | None = None
    market_hub_kind: HubKind | None = None
    market_hub_name: str | None = None
