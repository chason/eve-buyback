"""Appraisal computation + persistence (ADR-0014 immutable snapshot, ADR-0007
resolution, ADR-0021 computation). Orchestration only — the money math is in
`domain/pricing.py`, the prices come from the market use case, and persistence is in
the appraisals repository. Owns the commit."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.application import market
from app.application import structure_tokens as structure_tokens_app
from app.application.auth import AuthenticatedUser
from app.application.corporations import get_registered_corporation
from app.application.errors import (
    AppraisalNotFound,
    AppraisalTooLarge,
    AppraisalTooManyEsiTypes,
    DeliveryLocationInvalid,
    DeliveryLocationRequired,
    EmptyAppraisal,
    StructureMarketUnavailable,
)
from app.application.pricing import get_config
from app.config import get_settings
from app.data.records import (
    AppraisalRecord,
    AppraisalSummaryRecord,
    BuybackConfigRecord,
    CorporationRecord,
    MarketPriceRecord,
    SdeTypeRecord,
)
from app.data.repositories import appraisals as appraisals_repo
from app.data.repositories import buyback_locations as locations_repo
from app.data.repositories import corporations as corporations_repo
from app.data.repositories import pricing_rules as rules_repo
from app.data.repositories import sde as sde_repo
from app.data.repositories import structure_tokens as tokens_repo
from app.domain import pricing as pricing_domain
from app.domain.ids import generate_appraisal_id
from app.domain.market import (
    FUZZWORK_HUB_NAMES,
    HubDescriptor,
    resolve_market_source,
)
from app.domain.paste import MAX_APPRAISAL_ITEMS, parse_paste
from app.domain.roles import role_at_least
from app.plugins.cache import Cache
from app.plugins.esi_market import EsiMarketClient
from app.plugins.fuzzwork import FuzzworkClient
from app.plugins.sso import EveSsoClient
from app.plugins.token_cipher import TokenCipher


@dataclass(frozen=True)
class AppraisalItem:
    type_id: int
    quantity: int


@dataclass(frozen=True)
class _WorkItem:
    """A line to price. `type_id is None` means an unresolved pasted name —
    `fallback_name` carries it for the rejected line."""

    type_id: int | None
    fallback_name: str | None
    quantity: int
    reason: str | None = None


async def create_appraisal(
    session: AsyncSession,
    fuzzwork: FuzzworkClient,
    esi_market: EsiMarketClient,
    sso: EveSsoClient,
    cipher: TokenCipher,
    *,
    user: AuthenticatedUser,
    items: list[AppraisalItem],
    paste: str | None,
    delivery_location_id: str | None = None,
    cache: Cache | None = None,
    now: datetime,
) -> AppraisalRecord:
    corp = await get_registered_corporation(session, user.corporation_id)  # 404 if not
    config = await get_config(session, user.corporation_id)
    hub_id = config.market_hub_id
    default_hub = HubDescriptor(
        hub_id=hub_id,
        kind=config.market_hub_kind,
        region_id=config.market_region_id,
    )

    # Resolve the drop-off location up front (fail fast before pricing work): a member
    # must pick from the corp's accepted list, or — if the corp configured none — the
    # appraisal defaults to the market-hub station (ADR-0030).
    delivery = await _resolve_delivery(session, corp, config, delivery_location_id)

    work = await _gather_items(session, items, paste)
    if not work:
        raise EmptyAppraisal()
    if len(work) > MAX_APPRAISAL_ITEMS:
        raise AppraisalTooLarge()

    # Reference data + rules + prices, fetched once for the whole appraisal.
    type_ids = list({w.type_id for w in work if w.type_id is not None})
    types = await sde_repo.get_types(session, type_ids)
    parent_of = {
        g.market_group_id: g.parent_id
        for g in await sde_repo.list_market_groups(session)
    }
    rules = await rules_repo.list_rules(session, corp.id)

    def rule_hub(r) -> HubDescriptor | None:
        """A rule's hub override (ADR-0031), normalized to None when it matches the
        corp default — dedupes the fetch and keeps the line annotation quiet."""
        if r.market_hub_id is None:
            return None
        if (
            r.market_hub_id == default_hub.hub_id
            and r.market_hub_kind == default_hub.kind
        ):
            return None
        return HubDescriptor(
            hub_id=r.market_hub_id,
            kind=r.market_hub_kind,
            region_id=r.market_region_id,
        )

    type_rules = {
        r.target_id: pricing_domain.RuleSpec(
            r.basis, r.percentage, r.reprocess, r.compressed_only, r.accepted,
            hub=rule_hub(r),
        )
        for r in rules
        if r.enabled and r.target_kind == "type"
    }
    group_rules = {
        r.target_id: pricing_domain.RuleSpec(
            r.basis, r.percentage, r.reprocess, r.compressed_only, r.accepted,
            hub=rule_hub(r),
        )
        for r in rules
        if r.enabled and r.target_kind == "market_group"
    }
    # Display names for override hubs on appraisal lines (structures aren't in the
    # SDE, so the cached rule name is the source of truth).
    hub_names: dict[str, str] = dict(FUZZWORK_HUB_NAMES)
    for r in rules:
        if r.market_hub_id and r.market_hub_name:
            hub_names[r.market_hub_id] = r.market_hub_name

    def resolve(type_id: int) -> pricing_domain.ResolvedRule:
        sde_type = types.get(type_id)
        return pricing_domain.resolve_rule(
            type_id,
            sde_type.market_group_id if sde_type else None,
            type_rules=type_rules,
            group_rules=group_rules,
            parent_of=parent_of,
            default_basis=config.default_basis,
            default_percentage=config.default_percentage,
            default_accepted=config.default_accepted,
        )

    # Group the ids to price by the hub their resolved rule points at (ADR-0031);
    # most appraisals stay single-hub and this degenerates to one fetch.
    def hub_for(type_id: int) -> HubDescriptor:
        return resolve(type_id).hub or default_hub

    ids_by_hub: dict[HubDescriptor, set[int]] = {}
    for tid in type_ids:
        ids_by_hub.setdefault(hub_for(tid), set()).add(tid)

    # Ore lines whose resolved rule says "reprocess" are priced by their refined
    # minerals (ADR-0026); the minerals join the **ore's** hub bucket so they're
    # valued where the ore prices.
    reprocess_ore_ids = [
        tid
        for tid in type_ids
        if (t := types.get(tid)) is not None
        and t.category_id == pricing_domain.ORE_CATEGORY_ID
        and resolve(tid).reprocess
    ]
    materials_by_type = await sde_repo.get_type_materials(session, reprocess_ore_ids)
    mineral_ids: set[int] = set()
    for ore_id, mats in materials_by_type.items():
        ore_hub = hub_for(ore_id)
        for mid, _ in mats:
            ids_by_hub.setdefault(ore_hub, set()).add(mid)
            mineral_ids.add(mid)
    if mineral_ids:  # mineral names for the per-line breakdown
        types = {**types, **await sde_repo.get_types(session, list(mineral_ids))}

    # Bound the worst-case outbound ESI fan-out (#23, ADR-0035): each distinct type at a
    # non-Fuzzwork hub is its own live ESI lookup (the cold-type cache-miss path the
    # background refresh can't pre-warm), so cap how many a single appraisal can trigger.
    # Fuzzwork hubs batch into one request and are exempt (bounded by the 1000-item cap).
    esi_type_count = sum(
        len(ids)
        for hub, ids in ids_by_hub.items()
        if resolve_market_source(hub) != "fuzzwork"
    )
    if esi_type_count > get_settings().max_esi_types_per_appraisal:
        raise AppraisalTooManyEsiTypes()

    # One corp-level structure token covers every structure hub (ADR-0029); the
    # provider is only invoked for esi_structure fetches, so pass it to all of them.
    corp_esi_token_provider = None
    if any(h.kind == "structure" for h in ids_by_hub):
        # If the corp's structure token isn't working (never authorized, or flagged
        # failing — revoked grant / lost docking access, #68), block the appraisal with
        # an actionable error instead of silently returning unpriced "No market data"
        # lines: the member can't fix it, so point them at a manager re-authorize.
        token = await tokens_repo.get_for_corp(session, corp.id)
        if token is None or token.last_refresh_failed_at is not None:
            raise StructureMarketUnavailable()

        async def corp_esi_token_provider() -> str:
            return await structure_tokens_app.get_corp_esi_access_token(
                session, sso, corporation_uuid=corp.id, cipher=cipher
            )

    # The read-through cache commits each hub's price fill in its OWN unit of work (#21),
    # mid-flight, before this appraisal's commit below — the market cache is shared
    # infrastructure, the upsert is idempotent, and a warm cache without a saved appraisal
    # is benign. That independence is only safe while nothing business-related is pending
    # on `session`: every business write (the appraisal row) happens *after* this loop, so
    # each cache commit flushes only its own cache rows. Guard the invariant so a write
    # accidentally added above can't be silently swept into the cache's commit. (The cache
    # upsert is a Core statement, not ORM, so it never registers here itself.)
    assert not (session.new or session.dirty or session.deleted), (
        "pending business writes before market pricing would be committed by the "
        "read-through cache's independent unit of work (#21)"
    )

    # One read-through-cached fetch per hub; a failing hub degrades to its own cache
    # (its unpriced lines reject as "No market data") without affecting the others.
    # Merged (not assigned) per hub_id: two rules can reference the same hub through
    # descriptors that differ only in their save-time-cached region_id (SDE drift
    # between saves) — they bucket separately but must land in one price map.
    price_maps: dict[str, dict[int, MarketPriceRecord]] = {}
    for bucket_hub, ids in ids_by_hub.items():
        prices = await market.get_market_prices(
            session,
            fuzzwork,
            esi_market,
            hub=bucket_hub,
            type_ids=sorted(ids),
            now=now,
            corp_esi_token_provider=corp_esi_token_provider,
            cache=cache,
        )
        price_maps.setdefault(bucket_hub.hub_id, {}).update(
            {p.type_id: p for p in prices}
        )

    lines: list[dict] = []
    accepted_total = Decimal("0")
    rejected_count = 0
    for w in work:
        line = _compute_line(
            w, config, types, price_maps, default_hub, hub_names, resolve,
            materials_by_type,
        )
        lines.append(line)
        if line["status"] == "accepted":
            accepted_total += line["line_total"]
        else:
            rejected_count += 1

    record = await appraisals_repo.create_appraisal(
        session,
        public_id=generate_appraisal_id(),
        corporation_id=corp.id,
        created_by_character_id=user.character_id,
        created_by_character_name=user.character_name,
        market_hub_id=hub_id,
        delivery_location_id=delivery[0],
        delivery_location_name=delivery[1],
        accepted_total=accepted_total,
        rejected_count=rejected_count,
        request_json={
            "items": [
                {"type_id": it.type_id, "quantity": it.quantity} for it in items
            ],
            "paste": paste,
        },
        lines=lines,
    )
    await session.commit()
    return record


async def _gather_items(
    session: AsyncSession, items: list[AppraisalItem], paste: str | None
) -> list[_WorkItem]:
    """Combine structured items with parsed + name-resolved paste lines."""
    work = [
        _WorkItem(type_id=it.type_id, fallback_name=None, quantity=it.quantity)
        for it in items
    ]
    if paste and paste.strip():
        parsed = parse_paste(paste)
        by_name = await sde_repo.get_types_by_names(
            session, [p.name for p in parsed]
        )
        for p in parsed:
            matches = by_name.get(p.name.lower(), [])
            if len(matches) == 1:
                work.append(
                    _WorkItem(
                        type_id=matches[0].type_id,
                        fallback_name=None,
                        quantity=p.quantity,
                    )
                )
            else:
                # 0 matches → unknown; >1 → an ambiguous EVE-SDE duplicate name.
                # Never silently guess which type the paster meant — reject it.
                reason = (
                    f"Ambiguous name ({len(matches)} matches)"
                    if matches
                    else "Unknown item"
                )
                work.append(
                    _WorkItem(
                        type_id=None,
                        fallback_name=p.name,
                        quantity=p.quantity,
                        reason=reason,
                    )
                )
    return work


async def _resolve_delivery(
    session: AsyncSession,
    corp: CorporationRecord,
    config: BuybackConfigRecord,
    delivery_location_id: str | None,
) -> tuple[str, str]:
    """Resolve the (id, name) drop-off snapshot for an appraisal (ADR-0030). When the
    corp has accepted locations the member must pick one of them; otherwise the
    appraisal falls back to the corp's market-hub station."""
    locations = await locations_repo.list_for_corp(session, corp.id)
    if locations:
        if delivery_location_id is None:
            raise DeliveryLocationRequired()
        match = next(
            (loc for loc in locations if loc.location_id == delivery_location_id),
            None,
        )
        if match is None:
            raise DeliveryLocationInvalid()
        return match.location_id, match.name
    # No accepted locations configured → default to the pricing hub's station.
    name = (
        config.market_hub_name
        or FUZZWORK_HUB_NAMES.get(config.market_hub_id)
        or f"Station {config.market_hub_id}"
    )
    return config.market_hub_id, name


async def get_appraisal(
    session: AsyncSession, *, corporation_id: int, public_id: str
) -> AppraisalRecord:
    corp = await corporations_repo.get_by_eve_id(session, corporation_id)
    record = await appraisals_repo.get_by_public_id(session, public_id)
    # 404 for missing OR cross-corp — don't leak existence (ADR-0014).
    if record is None or corp is None or record.corporation_id != corp.id:
        raise AppraisalNotFound()
    return record


async def list_appraisals(
    session: AsyncSession, *, user: AuthenticatedUser
) -> list[AppraisalSummaryRecord]:
    corp = await corporations_repo.get_by_eve_id(session, user.corporation_id)
    if corp is None:
        return []
    if role_at_least(user.role, "manager"):
        return await appraisals_repo.list_for_corp(session, corp.id)
    return await appraisals_repo.list_for_character(
        session, corp.id, user.character_id
    )


def _basis_value(
    price: MarketPriceRecord | None, agg: str, basis: pricing_domain.Basis
) -> Decimal | None:
    """The market unit value for a basis from a price row (None if unavailable)."""
    if price is None:
        return None
    buy = getattr(price, f"buy_{agg}") if price.buy_order_count > 0 else None
    sell = getattr(price, f"sell_{agg}") if price.sell_order_count > 0 else None
    return pricing_domain.select_aggregate(buy, sell, basis)


def _compute_line(
    w: _WorkItem,
    config: BuybackConfigRecord,
    types: dict[int, SdeTypeRecord],
    price_maps: dict[str, dict[int, MarketPriceRecord]],
    default_hub: HubDescriptor,
    hub_names: dict[str, str],
    resolve: Callable[[int], pricing_domain.ResolvedRule],
    materials_by_type: dict[int, list[tuple[int, int]]],
) -> dict:
    if w.type_id is None:
        return _rejected(
            None, w.fallback_name or "Unknown", w.quantity, w.reason or "Unknown item"
        )

    sde_type = types.get(w.type_id)
    if sde_type is None:
        return _rejected(w.type_id, f"Type {w.type_id}", w.quantity, "Unknown item")

    resolved = resolve(w.type_id)

    # The hub this line prices at (ADR-0031); the snapshot is set only on overridden
    # lines so null keeps meaning "the appraisal's default hub".
    hub = resolved.hub or default_hub
    price_by_id = price_maps.get(hub.hub_id, {})
    hub_snapshot: dict = (
        {
            "market_hub_id": hub.hub_id,
            "market_hub_name": hub_names.get(hub.hub_id, f"Hub {hub.hub_id}"),
        }
        if resolved.hub is not None
        else {}
    )

    def rejected(reason: str) -> dict:
        return _rejected(w.type_id, sde_type.name, w.quantity, reason) | hub_snapshot

    # A blacklist rule (accepted=False) rejects the item outright (ADR-0007).
    if not resolved.accepted:
        return rejected("Not accepted")

    is_ore = sde_type.category_id == pricing_domain.ORE_CATEGORY_ID

    # "Compressed only" rule: reject the uncompressed variants of matched ores (ADR-0026).
    if (
        resolved.compressed_only
        and is_ore
        and not pricing_domain.is_compressed_ore(sde_type.name)
    ):
        return rejected("Compressed only")

    agg = config.aggregate_field
    materials = materials_by_type.get(w.type_id)
    breakdown: dict | None = None

    if resolved.reprocess and materials:
        # Reprocess pricing (ADR-0026): value whole refine batches by their minerals
        # and any sub-batch leftover at the ore's own price — all at the line's hub.
        mineral_value = {
            mid: _basis_value(price_by_id.get(mid), agg, resolved.basis)
            for mid, _ in materials
        }
        ore_unit = _basis_value(price_by_id.get(w.type_id), agg, resolved.basis)
        result = pricing_domain.reprocess_line(
            w.quantity, sde_type.portion_size, materials, mineral_value, ore_unit
        )
        if result is None:
            return rejected("No market data")
        unit_value = result.total / Decimal(w.quantity)
        breakdown = _reprocess_breakdown(result, types)
    else:
        price = price_by_id.get(w.type_id)
        if price is None:
            return rejected("No market data")
        unit_value = _basis_value(price, agg, resolved.basis)
        if unit_value is None or unit_value <= 0:
            return rejected(f"No {resolved.basis} orders")

    up = pricing_domain.unit_price(unit_value, resolved.percentage)
    lt = pricing_domain.line_total(up, w.quantity)
    return {
        "type_id": w.type_id,
        "type_name": sde_type.name,
        "quantity": w.quantity,
        "status": "accepted",
        "basis": resolved.basis,
        "percentage": resolved.percentage,
        "unit_value": unit_value,
        "unit_price": up,
        "line_total": lt,
        "reason": None,
        "reprocess": breakdown,
    } | hub_snapshot


def _reprocess_breakdown(
    result: pricing_domain.ReprocessResult, types: dict[int, SdeTypeRecord]
) -> dict:
    """JSON-serializable snapshot of a reprocess result for the appraisal line. Money
    and quantities are Decimal strings (ADR-0020)."""
    return {
        "minerals": [
            {
                "type_id": m.type_id,
                "type_name": types[m.type_id].name
                if m.type_id in types
                else f"Type {m.type_id}",
                "quantity": str(m.quantity),
                "unit_value": str(m.unit_value) if m.unit_value is not None else None,
                "value": str(m.value),
            }
            for m in result.minerals
        ],
        "leftover_units": result.leftover_units,
        "leftover_value": str(result.leftover_value),
    }


def _rejected(
    type_id: int | None, type_name: str, quantity: int, reason: str
) -> dict:
    return {
        "type_id": type_id,
        "type_name": type_name,
        "quantity": quantity,
        "status": "rejected",
        "basis": None,
        "percentage": None,
        "unit_value": None,
        "unit_price": None,
        "line_total": Decimal("0"),
        "market_hub_id": None,
        "market_hub_name": None,
        "reason": reason,
        "reprocess": None,
    }
