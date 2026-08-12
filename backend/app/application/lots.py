"""Lot-ledger use cases (ADR-0043).

Three pieces so far: ingestion (#151 — when the contract watcher, ADR-0037, confirms a
buyback contract completed, the appraisal's accepted lines become inventory lots with a
verified cost basis), the inventory view (#152 — what the corp owns now, at cost, and
what it would fetch today, #153), and the automatic write-down sweep (#153).

Ingestion is deliberately NOT gated by the accounting entitlement (ADR-0042): ESI only
surfaces recent contracts, so skipping unpaid corps would leave permanent holes in a
ledger they later pay to see. The paid gate stays on the read APIs.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.application import entitlements as entitlements_app
from app.application.corporations import get_registered_corporation
from app.application.reconciliation import location_names
from app.data.records import HangarStockRecord, LotRecord
from app.data.repositories import appraisals as appraisals_repo
from app.data.repositories import buyback_config as config_repo
from app.data.repositories import expenses as expenses_repo
from app.data.repositories import hangar_stock as hangar_stock_repo
from app.data.repositories import hangars as hangars_repo
from app.data.repositories import lots as lots_repo
from app.data.repositories import market_orders as orders_repo
from app.data.repositories import prices as prices_repo
from app.data.repositories import sde as sde_repo
from app.domain.contracts import ContractLink
from app.domain.lots import (
    LotSource,
    landed_unit_cost,
    nrv_per_unit,
    write_down_target,
)
from app.domain.pricing import ORE_CATEGORY_ID


async def _nrv_by_type(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    type_ids: list[int],
    sales_tax_rate: Decimal,
) -> dict[int, Decimal]:
    """Per type, what one unit would net if sold today (#153): the buy-side aggregate
    at the corp's configured default hub — the same field family appraisals price
    with — net of sales tax. Best-available *cached* value (ADR-0043): reads the
    `MarketPrice` cache as-is, never fetches live; unpriced/unwarmed types are simply
    absent and the callers surface that rather than invent a value."""
    if not type_ids:
        return {}
    config = await config_repo.get_config(session, corporation_id)
    if config is None:
        return {}
    prices = await prices_repo.get_prices(
        session, hub_id=config.market_hub_id, type_ids=type_ids
    )
    out: dict[int, Decimal] = {}
    for price in prices:
        buy = (
            getattr(price, f"buy_{config.aggregate_field}")
            if price.buy_order_count > 0
            else None
        )
        if buy is not None:
            out[price.type_id] = nrv_per_unit(buy, sales_tax_rate=sales_tax_rate)
    return out


class InventoryLotView(BaseModel):
    """One open lot as the inventory view shows it (#152): what's left, what one
    unit is carried at (landed, write-down floored), and how long it's been sitting.
    `id` lets the UI act on the lot (the reprocess record action, #177)."""

    id: uuid.UUID
    qty: int
    unit_cost: Decimal
    total_cost: Decimal
    acquired_at: datetime
    days_held: int
    stale: bool
    cost_is_estimated: bool
    # Provenance (#158): how the entry got into the books — surfaced so manual
    # entries and reprocess children carry a plain-English badge.
    source: LotSource


class StockLocationView(BaseModel):
    """Where a hangar-basis row physically sits: one entry per marked hangar
    location the snapshot counted the type at, named the way the reconciliation
    names locations (configured hangar name, else seeded station; None when
    neither resolves and the UI falls back to the raw id)."""

    location_id: str
    location_name: str | None
    qty: int


class InventoryItemView(BaseModel):
    """One item type's holdings: its open lots plus the rollup the table row shows.
    `type_name` is None when the type is missing from the seeded SDE. `worth` /
    `unrealized` (#153) are None when the type has no cached market price — surfaced,
    never invented.

    On the hangar basis (ADR-0050) `qty` is what's physically there, `lots` are the
    ledger entries backing it (portion-capped to the physical count), and
    `qty_unbooked` is the part no ledger entry explains yet — its age is unknown
    (`oldest_days` is None when NOTHING is booked) and it carries no cost, so
    `unrealized` compares the market's answer against the booked portion only."""

    type_id: int
    type_name: str | None
    qty: int
    qty_unbooked: int = 0
    total_cost: Decimal
    oldest_days: int | None
    stale: bool
    any_estimated: bool
    worth: Decimal | None = None
    unrealized: Decimal | None = None
    # Whether the type has any seeded reprocessing yields — i.e. can be
    # reprocessed at all; gates the "Turned into minerals" action (#177).
    reprocessable: bool
    # Whether the type is an ore (SDE category 25) — lets the table fold small
    # leftover ore stacks out of view.
    is_ore: bool
    # Which hangar(s) the stack physically sits in, biggest count first — hangar
    # basis only (empty on the ledger basis, where rows aren't placed).
    locations: list[StockLocationView] = []
    lots: list[InventoryLotView]


class ListedStockView(BaseModel):
    """One stack of the corp's stock sitting in sell-order escrow (ADR-0050):
    physically out of the hangar but still owned, so the Stock page lists it in
    its own small section rather than letting it vanish from view. `worth` is the
    same net-of-tax market answer the main table uses (None when unpriced)."""

    type_id: int
    type_name: str | None
    location_id: str
    location_name: str | None
    qty: int
    worth: Decimal | None = None


class InventoryView(BaseModel):
    """The whole "What we've got" view (ADR-0043, #152/#153): inventory carried at
    cost, with verified and estimated cost kept apart so they never silently blend.
    `worth_total` is "if we sold it all today" (net of sales tax) and
    `unrealized_total` the paper gain/loss — its OWN line, never folded into assets;
    both cover only the OPEN LOTS the market cache can price. `anything_priced`
    says whether any open lot is priced at all (the valuation cards' gate) —
    ledger-wide, like the totals it gates. `unpriced_types` counts the DISPLAYED
    table rows without a price (the table's footnote), which on the hangar basis
    is a different population than the totals'.

    Two bases (ADR-0050): `hangar` — `items` are what the last hangar snapshot
    actually counted in the marked buyback hangars (taken `as_of`), with the ledger
    joined in for cost and age, and `listed` carrying the sell-order stock that is
    physically elsewhere; `ledger` — the pre-ADR-0050 books view, used while no
    hangar snapshot exists (no marked hangars, or no sync yet). The summary totals
    are ALWAYS ledger-wide ("everything we hold", wherever it sits) so switching
    basis never changes what the corp owns on paper."""

    basis: Literal["hangar", "ledger"]
    as_of: datetime | None = None
    total_cost: Decimal
    verified_cost: Decimal
    estimated_cost: Decimal
    stale_days: int
    worth_total: Decimal
    unrealized_total: Decimal
    anything_priced: bool
    unpriced_types: int
    items: list[InventoryItemView]
    listed: list[ListedStockView] = []


async def get_inventory(
    session: AsyncSession,
    *,
    corporation_eve_id: int,
    stale_days: int,
    sales_tax_rate: Decimal,
    now: datetime | None = None,
) -> InventoryView:
    """The "What we've got" view (#152/#153, ADR-0050). When a hangar snapshot
    exists, the table shows what is PHYSICALLY in the marked buyback hangars —
    minerals show as minerals, whatever the contracts delivered — with the open
    lots joined in FIFO for cost and age (a lot's age reaches back to the contract
    that bought it). Stock sitting in sell-order escrow rides along in `listed`.
    Without a snapshot it falls back to the ledger view: every open lot rolled up
    per type. Either way lots sitting `stale_days` or longer are flagged, items
    sort biggest-holdings-first, and the summary totals cover every open lot.
    Gated: the accounting entitlement is required (ADR-0042)."""
    corp = await get_registered_corporation(session, corporation_eve_id)
    await entitlements_app.require_entitlement(
        session, corporation_id=corp.id, feature="accounting", now=now
    )
    now = now or datetime.now(UTC)

    lots = await lots_repo.open_lots(session, corporation_id=corp.id)
    synced_at = None
    if await hangars_repo.list_for_corp(session, corp.id):
        synced_at = await hangar_stock_repo.synced_at(session, corporation_id=corp.id)
    snapshot: list[HangarStockRecord] = []
    listed_map: dict[tuple[str, int], int] = {}
    if synced_at is not None:
        snapshot = await hangar_stock_repo.list_for_corp(
            session, corporation_id=corp.id
        )
        listed_map = await orders_repo.listed_by_location_type(
            session, corporation_id=corp.id
        )

    lot_types = {lot.item_type_id for lot in lots}
    snapshot_types = {row.type_id for row in snapshot}
    listed_types = {tid for _, tid in listed_map}
    type_ids = sorted(lot_types | snapshot_types | listed_types)
    names = await sde_repo.get_types(session, type_ids)
    reprocessable_ids = await sde_repo.types_with_materials(session, type_ids)
    nrv = await _nrv_by_type(
        session,
        corporation_id=corp.id,
        type_ids=type_ids,
        sales_tax_rate=sales_tax_rate,
    )

    if synced_at is not None:
        places = await location_names(
            session, corp.id, {row.location_id for row in snapshot}
        )
        items = _hangar_items(
            lots, snapshot, places, names, reprocessable_ids, nrv, now, stale_days
        )
        listed = await _listed_stock(session, corp.id, listed_map, names, nrv)
    else:
        items = _ledger_items(lots, names, reprocessable_ids, nrv, now, stale_days)
        listed = []
    items.sort(key=_item_sort_key)

    totals = _ledger_totals(lots, nrv, now, stale_days)
    return InventoryView(
        basis="hangar" if synced_at is not None else "ledger",
        as_of=synced_at,
        total_cost=totals["total"],
        verified_cost=totals["total"] - totals["estimated"],
        estimated_cost=totals["estimated"],
        stale_days=stale_days,
        worth_total=totals["worth"],
        unrealized_total=totals["unrealized"],
        # The cards' gate follows the totals' population (open lots), not the
        # table's — an empty hangar must not hide a priced ledger (ADR-0050).
        anything_priced=any(lot.item_type_id in nrv for lot in lots),
        unpriced_types=sum(1 for item in items if item.worth is None),
        items=items,
        listed=listed,
    )


def _lot_view(
    lot: LotRecord, qty: int, now: datetime, stale_days: int
) -> InventoryLotView:
    """One lot as the table's expander shows it, for `qty` of its units — the full
    remainder on the ledger basis, the physically-present portion on the hangar
    basis (ADR-0050)."""
    unit_cost = landed_unit_cost(
        lot.unit_purchase_cost, lot.unit_hauling_cost, lot.written_down_to
    )
    days_held = max(0, (now - lot.acquired_at).days)
    return InventoryLotView(
        id=lot.id,
        source=lot.source,
        qty=qty,
        unit_cost=unit_cost,
        total_cost=qty * unit_cost,
        acquired_at=lot.acquired_at,
        days_held=days_held,
        stale=days_held >= stale_days,
        cost_is_estimated=lot.cost_is_estimated,
    )


def _item_view(
    type_id: int,
    qty: int,
    qty_unbooked: int,
    views: list[InventoryLotView],
    names: dict,
    reprocessable_ids: set[int],
    nrv: dict[int, Decimal],
    locations: list[StockLocationView] | None = None,
) -> InventoryItemView:
    """The rollup a table row shows. `worth` prices the whole quantity shown;
    `unrealized` compares only the booked portion against its cost — an unbooked
    stack has no basis to be up or down against (ADR-0050)."""
    total_cost = sum((v.total_cost for v in views), Decimal(0))
    unit = nrv.get(type_id)
    worth = qty * unit if unit is not None else None
    unrealized = None
    if unit is not None:
        unrealized = (qty - qty_unbooked) * unit - total_cost
    days = [v.days_held for v in views]
    sde_type = names.get(type_id)
    return InventoryItemView(
        type_id=type_id,
        type_name=sde_type.name if sde_type else None,
        qty=qty,
        qty_unbooked=qty_unbooked,
        total_cost=total_cost,
        oldest_days=max(days) if days else None,
        stale=any(v.stale for v in views),
        any_estimated=any(v.cost_is_estimated for v in views),
        worth=worth,
        unrealized=unrealized,
        reprocessable=type_id in reprocessable_ids,
        is_ore=sde_type is not None and sde_type.category_id == ORE_CATEGORY_ID,
        locations=locations or [],
        lots=views,
    )


def _ledger_items(
    lots: Sequence[LotRecord],
    names: dict,
    reprocessable_ids: set[int],
    nrv: dict[int, Decimal],
    now: datetime,
    stale_days: int,
) -> list[InventoryItemView]:
    """The books view (pre-ADR-0050 fallback): every open lot, rolled up per type."""
    by_type: dict[int, list[InventoryLotView]] = {}
    for lot in lots:  # already FIFO-ordered: oldest acquired first
        view = _lot_view(lot, lot.qty_remaining, now, stale_days)
        by_type.setdefault(lot.item_type_id, []).append(view)
    items = []
    for type_id, views in by_type.items():
        qty = sum(v.qty for v in views)
        items.append(
            _item_view(type_id, qty, 0, views, names, reprocessable_ids, nrv)
        )
    return items


def _hangar_items(
    lots: Sequence[LotRecord],
    snapshot: Sequence[HangarStockRecord],
    places: dict[str, str],
    names: dict,
    reprocessable_ids: set[int],
    nrv: dict[int, Decimal],
    now: datetime,
    stale_days: int,
) -> list[InventoryItemView]:
    """The hangar view (ADR-0050): one row per type the snapshot counted, backed by
    the open lots at the same `(location, type)` slot, matched FIFO and capped at
    the physical count. Lot units beyond the count are NOT shown here — they are
    the reconciliation's business (a standing shortfall flag, a move card, escrow);
    physical units beyond the lots surface as `qty_unbooked`."""
    lots_by_slot: dict[tuple[str, int], list[LotRecord]] = {}
    for lot in lots:  # FIFO order within each slot is inherited
        if lot.location_id is not None:
            slot = (lot.location_id, lot.item_type_id)
            lots_by_slot.setdefault(slot, []).append(lot)

    qty_by_type: dict[int, int] = {}
    unbooked_by_type: dict[int, int] = {}
    views_by_type: dict[int, list[InventoryLotView]] = {}
    locations_by_type: dict[int, list[StockLocationView]] = {}
    for row in snapshot:
        remaining = row.qty
        views = views_by_type.setdefault(row.type_id, [])
        for lot in lots_by_slot.get((row.location_id, row.type_id), []):
            if remaining <= 0:
                break
            take = min(remaining, lot.qty_remaining)
            views.append(_lot_view(lot, take, now, stale_days))
            remaining -= take
        qty_by_type[row.type_id] = qty_by_type.get(row.type_id, 0) + row.qty
        unbooked_by_type[row.type_id] = (
            unbooked_by_type.get(row.type_id, 0) + remaining
        )
        locations_by_type.setdefault(row.type_id, []).append(
            StockLocationView(
                location_id=row.location_id,
                location_name=places.get(row.location_id),
                qty=row.qty,
            )
        )

    items = []
    for type_id, qty in qty_by_type.items():
        views = views_by_type[type_id]
        views.sort(key=lambda v: (v.acquired_at, v.id))  # FIFO across hangars
        locations = locations_by_type[type_id]
        locations.sort(key=_location_sort_key)  # biggest count first
        items.append(
            _item_view(
                type_id,
                qty,
                unbooked_by_type[type_id],
                views,
                names,
                reprocessable_ids,
                nrv,
                locations,
            )
        )
    return items


def _location_sort_key(loc: StockLocationView):
    return (-loc.qty, loc.location_name or "", loc.location_id)


async def _listed_stock(
    session: AsyncSession,
    corporation_id: uuid.UUID,
    listed_map: dict[tuple[str, int], int],
    names: dict,
    nrv: dict[int, Decimal],
) -> list[ListedStockView]:
    """The "listed for sale" section (ADR-0050): sell-order escrow per station —
    owned stock that is physically out of the hangar. Location names resolve like
    the reconciliation's (configured hangar name, else seeded SDE station)."""
    if not listed_map:
        return []
    location_ids = {loc for loc, _ in listed_map}
    locations = await location_names(session, corporation_id, location_ids)
    rows = []
    for (location_id, type_id), qty in sorted(listed_map.items()):
        unit = nrv.get(type_id)
        rows.append(
            ListedStockView(
                type_id=type_id,
                type_name=names[type_id].name if type_id in names else None,
                location_id=location_id,
                location_name=locations.get(location_id),
                qty=qty,
                worth=qty * unit if unit is not None else None,
            )
        )
    rows.sort(key=_listed_sort_key)
    return rows


def _ledger_totals(
    lots: Sequence[LotRecord],
    nrv: dict[int, Decimal],
    now: datetime,
    stale_days: int,
) -> dict[str, Decimal]:
    """The summary cards, ALWAYS over every open lot (ADR-0050): what the corp
    owns on paper doesn't change with the table's basis. `worth`/`unrealized`
    cover only the lots whose type the market cache can price (#153)."""
    total = Decimal(0)
    estimated = Decimal(0)
    worth = Decimal(0)
    unrealized = Decimal(0)
    for lot in lots:
        view = _lot_view(lot, lot.qty_remaining, now, stale_days)
        total += view.total_cost
        if view.cost_is_estimated:
            estimated += view.total_cost
        unit = nrv.get(lot.item_type_id)
        if unit is not None:
            worth += view.qty * unit
            unrealized += view.qty * unit - view.total_cost
    return {
        "total": total,
        "estimated": estimated,
        "worth": worth,
        "unrealized": unrealized,
    }


def _item_sort_key(item: InventoryItemView):
    """Biggest holdings first: by cost, then market worth (covers all-unbooked
    rows, which carry no cost), then a stable name/type tiebreak."""
    return (
        -item.total_cost,
        -(item.worth if item.worth is not None else Decimal(0)),
        item.type_name or "",
        item.type_id,
    )


def _listed_sort_key(row: ListedStockView):
    return (
        -(row.worth if row.worth is not None else Decimal(0)),
        row.type_name or "",
        row.type_id,
    )


async def apply_write_downs(
    session: AsyncSession,
    *,
    corporation_eve_id: int,
    sales_tax_rate: Decimal,
    now: datetime | None = None,
) -> int:
    """The conservatism sweep (ADR-0043, #153): for every open lot whose current
    market value (NRV) fell below its carried cost, floor the carried value
    (`written_down_to = NRV`) and book the loss as a `write_down` expense in this
    period. Never reverses upward — a later price rise shows as unrealized gain from
    the floored base, not as a restored cost. Idempotent at stable prices: once
    floored, landed cost == NRV, so no further target exists until prices drop
    again (which books only the *incremental* loss). Returns lots written down.

    Run by the background job for entitled corps; the job owns the corp filter, so
    this stays callable per corp. Owns its unit of work."""
    corp = await get_registered_corporation(session, corporation_eve_id)
    now = now or datetime.now(UTC)
    lots = await lots_repo.open_lots(session, corporation_id=corp.id)
    if not lots:
        return 0
    nrv = await _nrv_by_type(
        session,
        corporation_id=corp.id,
        type_ids=sorted({lot.item_type_id for lot in lots}),
        sales_tax_rate=sales_tax_rate,
    )

    written_down = 0
    for lot in lots:
        value = nrv.get(lot.item_type_id)
        if value is None:
            continue  # no cached price → no evidence to book a loss on
        landed = landed_unit_cost(
            lot.unit_purchase_cost, lot.unit_hauling_cost, lot.written_down_to
        )
        target = write_down_target(landed, value)
        if target is None:
            continue
        await lots_repo.write_down(session, lot_id=lot.id, value=target)
        await expenses_repo.create_expense(
            session,
            corporation_id=corp.id,
            kind="write_down",
            amount=(landed - target) * lot.qty_remaining,
            source="system",
            incurred_at=now,
            lot_id=lot.id,
            note="Stock is worth less than we paid; its value was lowered to match "
            "the market.",
        )
        written_down += 1
    if written_down:
        await session.commit()
    return written_down


async def materialize_buyback_lots(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    links: Sequence[ContractLink],
    now: datetime,
) -> int:
    """Create the lots for appraisals whose contract just completed (#151): one lot per
    accepted line at the exact price paid (`unit_price` — so `cost_is_estimated` stays
    False), sitting at the appraisal's delivery location, acquired when the contract
    completed. Idempotent per appraisal: `completed` is terminal for lot creation, so
    an appraisal that already has lots is never touched again, whatever the watcher
    later observes. Returns the number of lots created.

    Runs inside the watcher's unit of work — the caller owns the commit, so the link
    update and the lots it implies land atomically."""
    pending = [
        link
        for link in links
        if link.status == "completed"
        and not await lots_repo.exists_for_appraisal(session, link.appraisal_id)
    ]
    if not pending:
        return 0

    ids = [link.appraisal_id for link in pending]
    lines_by_appraisal = await appraisals_repo.accepted_lines_for_lots(session, ids)
    facts = await appraisals_repo.match_facts(session, ids)

    created = 0
    for link in pending:
        _, location_id = facts.get(link.appraisal_id, (None, None))
        for line in lines_by_appraisal.get(link.appraisal_id, []):
            await lots_repo.create_lot(
                session,
                corporation_id=corporation_id,
                item_type_id=line.type_id,
                qty=line.quantity,
                unit_purchase_cost=line.unit_price,
                acquired_at=link.completed_at or now,
                source="buyback",
                appraisal_id=link.appraisal_id,
                location_id=location_id,
            )
            created += 1
    return created
