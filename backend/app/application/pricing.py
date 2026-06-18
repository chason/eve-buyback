"""Buyback config + pricing rule use cases (ADR-0007). Manager gating is enforced
at the interface; these enforce existence/uniqueness/target rules and own the commit.
"""

import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.corporations import get_registered_corporation
from app.application.errors import (
    MarketHubInvalid,
    PricingRuleNotFound,
    PricingRuleTargetInvalid,
)
from app.config import get_settings
from app.data.records import BuybackConfigRecord, PricingRuleRecord
from app.data.repositories import buyback_config as config_repo
from app.data.repositories import corp_esi_token as corp_esi_token_repo
from app.data.repositories import pricing_rules as rules_repo
from app.data.repositories import sde as sde_repo
from app.domain.market import (
    FUZZWORK_HUB_NAMES,
    HubDescriptor,
    HubKind,
    resolve_market_source,
)
from app.domain.pricing import (
    DEFAULT_AGGREGATE_FIELD,
    DEFAULT_BASIS,
    DEFAULT_PERCENTAGE,
    AggregateField,
    Basis,
    TargetKind,
)


async def get_config(
    session: AsyncSession, corporation_id: int
) -> BuybackConfigRecord:
    """Return the corp's config (404 if the corp isn't registered). Lazily creates
    the default config if missing — registration normally creates it."""
    corp = await get_registered_corporation(session, corporation_id)
    config = await config_repo.get_config(session, corp.id)
    if config is None:
        config = await config_repo.upsert_config(
            session,
            corporation_id=corp.id,
            market_hub_id=str(get_settings().market_hub_id),
            default_basis=DEFAULT_BASIS,
            default_percentage=DEFAULT_PERCENTAGE,
            aggregate_field=DEFAULT_AGGREGATE_FIELD,
        )
        await session.commit()
    return config


async def update_config(
    session: AsyncSession,
    corporation_id: int,
    *,
    market_hub_id: str,
    default_basis: Basis,
    default_percentage: Decimal,
    aggregate_field: AggregateField,
    default_accepted: bool = True,
    market_hub_kind: HubKind = "npc_station",
    market_hub_name: str | None = None,
) -> BuybackConfigRecord:
    corp = await get_registered_corporation(session, corporation_id)
    region_id, hub_name = await _resolve_hub(
        session, corp.id, market_hub_id, market_hub_kind, market_hub_name
    )
    config = await config_repo.upsert_config(
        session,
        corporation_id=corp.id,
        market_hub_id=market_hub_id,
        market_hub_kind=market_hub_kind,
        market_region_id=region_id,
        market_hub_name=hub_name,
        default_basis=default_basis,
        default_percentage=default_percentage,
        aggregate_field=aggregate_field,
        default_accepted=default_accepted,
    )
    await session.commit()
    return config


async def _resolve_hub(
    session: AsyncSession,
    corporation_uuid: uuid.UUID,
    hub_id: str,
    kind: HubKind,
    name_hint: str | None = None,
) -> tuple[int | None, str | None]:
    """Resolve a chosen hub to `(region_id, display_name)`, validating it (ADR-0028,
    ADR-0029). Fuzzwork hubs need no region; a non-Fuzzwork NPC station is resolved
    from the seeded SDE; a structure hub requires the corp to have authorized
    structure access first (region is unused — the structure endpoint is
    structure-scoped). Raises `MarketHubInvalid` (422) if it can't be resolved."""
    if kind == "structure":
        # Numeric-only: the id is interpolated into an authenticated ESI URL when
        # pricing, so reject anything that could inject a path (e.g. "1/../x").
        if not hub_id.isdigit():
            raise MarketHubInvalid(f"Invalid structure id {hub_id!r}")
        token = await corp_esi_token_repo.get_for_corp(session, corporation_uuid)
        if token is None:
            raise MarketHubInvalid(
                "Authorize structure access before selecting a structure hub"
            )
        # No SDE for structures — keep the friendly name the client got from the
        # structure search, falling back to the id if it wasn't supplied.
        return None, name_hint or f"Structure {hub_id}"
    source = resolve_market_source(HubDescriptor(hub_id=hub_id, kind=kind))
    if source == "fuzzwork":
        return None, FUZZWORK_HUB_NAMES.get(hub_id)
    try:
        station_id = int(hub_id)
    except ValueError as exc:
        raise MarketHubInvalid(f"Invalid station id {hub_id!r}") from exc
    station = await sde_repo.get_station(session, station_id)
    if station is None:
        raise MarketHubInvalid(f"Unknown NPC station {hub_id}")
    return station.region_id, f"{station.system_name} - {station.name}"


async def list_rules(
    session: AsyncSession, corporation_id: int
) -> list[PricingRuleRecord]:
    corp = await get_registered_corporation(session, corporation_id)
    rules = await rules_repo.list_rules(session, corp.id)
    return await _with_target_names(session, rules)


async def _with_target_names(
    session: AsyncSession, rules: list[PricingRuleRecord]
) -> list[PricingRuleRecord]:
    """Resolve each rule's target to its SDE name for display. Batched: one type
    lookup and one market-group lookup for the whole list."""
    type_ids = [r.target_id for r in rules if r.target_kind == "type"]
    types = await sde_repo.get_types(session, type_ids) if type_ids else {}
    group_names = {
        g.market_group_id: g.name
        for g in await sde_repo.list_market_groups(session)
    }

    def name_for(rule: PricingRuleRecord) -> str | None:
        if rule.target_kind == "type":
            t = types.get(rule.target_id)
            return t.name if t else None
        return group_names.get(rule.target_id)

    return [r.model_copy(update={"target_name": name_for(r)}) for r in rules]


async def set_rule(
    session: AsyncSession,
    *,
    corporation_id: int,
    target_kind: TargetKind,
    target_id: int,
    basis: Basis | None,
    percentage: Decimal,
    enabled: bool,
    reprocess: bool,
    compressed_only: bool,
    accepted: bool,
    market_hub_id: str | None = None,
    market_hub_kind: HubKind | None = None,
    market_hub_name: str | None = None,
) -> tuple[PricingRuleRecord, bool]:
    """Create or replace the corp's rule for a target (idempotent PUT). Returns
    `(record, created)`. The target must exist (else 400); there is no 409/404 on
    write — setting the rule for a target is the whole operation. A hub override
    (ADR-0031) is validated and resolved like the config hub; PUT is
    full-replacement, so a request without one clears it."""
    corp = await get_registered_corporation(session, corporation_id)
    target_name = await _validate_target(session, target_kind, target_id)

    region_id: int | None = None
    hub_name: str | None = None
    hub_kind: HubKind | None = None
    if market_hub_id is not None:
        hub_kind = market_hub_kind or "npc_station"
        region_id, hub_name = await _resolve_hub(
            session, corp.id, market_hub_id, hub_kind, market_hub_name
        )

    record, created = await rules_repo.upsert_rule(
        session,
        corporation_id=corp.id,
        target_kind=target_kind,
        target_id=target_id,
        basis=basis,
        percentage=percentage,
        enabled=enabled,
        reprocess=reprocess,
        compressed_only=compressed_only,
        accepted=accepted,
        market_hub_id=market_hub_id,
        market_hub_kind=hub_kind,
        market_region_id=region_id,
        market_hub_name=hub_name,
    )
    await session.commit()
    return record.model_copy(update={"target_name": target_name}), created


async def delete_rule(
    session: AsyncSession,
    *,
    corporation_id: int,
    target_kind: TargetKind,
    target_id: int,
) -> None:
    corp = await get_registered_corporation(session, corporation_id)
    removed = await rules_repo.delete_rule(
        session,
        corporation_id=corp.id,
        target_kind=target_kind,
        target_id=target_id,
    )
    if not removed:
        raise PricingRuleNotFound()
    await session.commit()


async def _validate_target(
    session: AsyncSession, target_kind: TargetKind, target_id: int
) -> str:
    """Ensure the target exists (else 400) and return its SDE name."""
    if target_kind == "type":
        sde_type = await sde_repo.get_type(session, target_id)
        if sde_type is None:
            raise PricingRuleTargetInvalid(f"Unknown type {target_id}")
        return sde_type.name
    group = await sde_repo.get_market_group(session, target_id)
    if group is None:
        raise PricingRuleTargetInvalid(f"Unknown market group {target_id}")
    return group.name
