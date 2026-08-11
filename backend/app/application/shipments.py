"""Declared-shipment use cases (ADR-0049, #208): a manager records a haul
proactively — pick a type and quantity at the origin hangar, the stock goes in
transit, then mark it arrived at the destination. The disciplined path that
preempts the move heuristic: while a shipment is open the reconciliation
excludes its quantity from idle at both ends (ADR-0043's in-transit
allocation), so the haul is never flagged and never becomes a "looks like a
move" pairing.

Modeled as an aggregate allocation — one `shipments` row per haul, no per-lot
rows — because that is the shape the codebase already leans toward: the
"listed" allocation is the aggregate sell-order snapshot, and lot state is
derived, never stored (ADR-0043). Lots stay at the origin until arrival
relocates them FIFO via the same `move_to_location` mechanic as a confirmed
move: cost basis, acquisition dates, estimated flags, and write-down floors
carried unchanged. A move is not an acquisition — carrying value never changes.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.application import entitlements as entitlements_app
from app.application.corporations import get_registered_corporation
from app.application.errors import (
    ShipmentAlreadyArrived,
    ShipmentHangarUnknown,
    ShipmentNotFound,
    ShipmentSameHangar,
    ShipmentStockUnavailable,
)
from app.data.records import ShipmentRecord
from app.data.repositories import hangars as hangars_repo
from app.data.repositories import lots as lots_repo
from app.data.repositories import market_orders as orders_repo
from app.data.repositories import reconciliation as recon_repo
from app.data.repositories import sde as sde_repo
from app.data.repositories import shipments as shipments_repo
from app.domain.lots import OpenLot, plan_fifo, qty_idle


@dataclass(frozen=True)
class ShipmentView:
    """An open haul enriched for display: names resolved so the UI stays
    plain (the `MoveSuggestionView` shape)."""

    record: ShipmentRecord
    type_name: str | None
    origin_name: str | None
    destination_name: str | None


@dataclass(frozen=True)
class ArrivalResult:
    """What the arrival relocated. Normally the shipment's quantity; less when
    the origin no longer holds it all (stock sold or moved mid-transit — the
    arrival caps at what actually sits there, nothing is invented)."""

    qty_moved: int


async def record_shipment(
    session: AsyncSession,
    *,
    corporation_eve_id: int,
    type_id: int,
    qty: int,
    origin_location_id: str,
    destination_location_id: str,
    recorded_by_character_id: int,
    recorded_by_name: str | None = None,
    now: datetime | None = None,
) -> ShipmentRecord:
    """Open a haul (ADR-0049, #208). Owns the commit.

    Guards: both ends must be marked buyback hangars (anywhere else the checks
    don't watch, so the declaration couldn't keep the books honest), and the
    quantity can't exceed what sits idle at the origin — open stock minus the
    listed sell-order escrow and earlier hauls still on the road. The log entry
    is written at the ORIGIN slot: that's where the stock left."""
    corp = await get_registered_corporation(session, corporation_eve_id)
    await entitlements_app.require_entitlement(
        session, corporation_id=corp.id, feature="accounting"
    )
    if origin_location_id == destination_location_id:
        raise ShipmentSameHangar()
    hangar_names = await _hangar_names(session, corp.id)
    if (
        origin_location_id not in hangar_names
        or destination_location_id not in hangar_names
    ):
        raise ShipmentHangarUnknown()

    origin_lots = await lots_repo.open_lots(
        session,
        corporation_id=corp.id,
        item_type_id=type_id,
        location_id=origin_location_id,
    )
    listed = await orders_repo.listed_by_location_type(
        session, corporation_id=corp.id
    )
    in_transit = await shipments_repo.open_by_origin_type(
        session, corporation_id=corp.id
    )
    slot = (origin_location_id, type_id)
    idle = qty_idle(
        sum(lot.qty_remaining for lot in origin_lots),
        on_orders=listed.get(slot, 0),
        in_transit=in_transit.get(slot, 0),
    )
    if qty > idle:
        raise ShipmentStockUnavailable()

    record = await shipments_repo.add(
        session,
        corporation_id=corp.id,
        type_id=type_id,
        origin_location_id=origin_location_id,
        destination_location_id=destination_location_id,
        qty=qty,
    )
    destination = hangar_names[destination_location_id]
    who = recorded_by_name or f"character {recorded_by_character_id}"
    await recon_repo.add_event(
        session,
        corporation_id=corp.id,
        location_id=origin_location_id,
        type_id=type_id,
        kind="shipment_recorded",
        qty=qty,
        occurred_at=now or datetime.now(UTC),
        note=f"on its way to {destination}, sent by {who}",
    )
    await session.commit()
    return record


async def mark_arrived(
    session: AsyncSession,
    *,
    corporation_eve_id: int,
    shipment_id: uuid.UUID,
    marked_by_character_id: int,
    marked_by_name: str | None = None,
    now: datetime | None = None,
) -> ArrivalResult:
    """Close a haul (ADR-0049, #208) — one unit of work: the origin's oldest
    open lots of the type relocate to the destination FIFO (splitting a lot on
    a partial take — the `confirm_move`/ADR-0047 mechanic, every cost field
    carried unchanged), the shipment closes, and the arrival logs at the
    DESTINATION slot: that's where the stock landed. Owns the commit.

    The relocation caps at what the origin still holds (`plan_fifo` reports the
    shortfall) — stock sold mid-transit is simply gone, and the next hangar
    check tells that story at the destination."""
    corp = await get_registered_corporation(session, corporation_eve_id)
    await entitlements_app.require_entitlement(
        session, corporation_id=corp.id, feature="accounting"
    )
    shipment = await shipments_repo.get_for_corp(
        session, corporation_id=corp.id, shipment_id=shipment_id
    )
    if shipment is None:
        raise ShipmentNotFound()
    if shipment.status == "arrived":
        raise ShipmentAlreadyArrived()
    now = now or datetime.now(UTC)

    origin_lots = await lots_repo.open_lots(
        session,
        corporation_id=corp.id,
        item_type_id=shipment.type_id,
        location_id=shipment.origin_location_id,
    )
    open_lots = [_open_lot(lot) for lot in origin_lots]
    plan = plan_fifo(open_lots, shipment.qty)
    moved = shipment.qty - plan.shortfall
    for consumption in plan.consumptions:
        await lots_repo.move_to_location(
            session,
            lot_id=consumption.lot_id,
            qty=consumption.qty,
            location_id=shipment.destination_location_id,
        )

    await shipments_repo.mark_arrived(
        session, shipment_id=shipment.id, arrived_at=now
    )
    hangar_names = await _hangar_names(session, corp.id)
    origin = hangar_names.get(
        shipment.origin_location_id, shipment.origin_location_id
    )
    who = marked_by_name or f"character {marked_by_character_id}"
    await recon_repo.add_event(
        session,
        corporation_id=corp.id,
        location_id=shipment.destination_location_id,
        type_id=shipment.type_id,
        kind="shipment_arrived",
        qty=moved,
        occurred_at=now,
        note=f"hauled from {origin}, marked arrived by {who}",
    )
    await session.commit()
    return ArrivalResult(qty_moved=moved)


async def list_open_shipments(
    session: AsyncSession, *, corporation_eve_id: int
) -> list[ShipmentView]:
    """The open hauls for the Stock page, names resolved. Gated: the accounting
    entitlement is required (ADR-0042)."""
    corp = await get_registered_corporation(session, corporation_eve_id)
    await entitlements_app.require_entitlement(
        session, corporation_id=corp.id, feature="accounting"
    )
    records = await shipments_repo.list_open(session, corporation_id=corp.id)
    types = await sde_repo.get_types(session, sorted({r.type_id for r in records}))
    hangar_names = await _hangar_names(session, corp.id)
    return [_shipment_view(r, types, hangar_names) for r in records]


def _shipment_view(
    record: ShipmentRecord, types: dict, hangar_names: dict[str, str]
) -> ShipmentView:
    type_name = types[record.type_id].name if record.type_id in types else None
    return ShipmentView(
        record=record,
        type_name=type_name,
        origin_name=hangar_names.get(record.origin_location_id),
        destination_name=hangar_names.get(record.destination_location_id),
    )


def _open_lot(lot) -> OpenLot:
    return OpenLot(
        lot_id=lot.id,
        qty_remaining=lot.qty_remaining,
        acquired_at=lot.acquired_at,
    )


async def _hangar_names(
    session: AsyncSession, corporation_id: uuid.UUID
) -> dict[str, str]:
    return {
        h.location_id: h.location_name
        for h in await hangars_repo.list_for_corp(session, corporation_id)
    }
