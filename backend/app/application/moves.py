"""Move-suggestion actions (ADR-0049, #201): a manager confirms a "looks like a
move" pairing, and the sync's default treatment converts retroactively — in one
unit of work: the deemed-cost excess lot at the destination is reversed for the
paired quantity, the oldest idle lots of that type at the origin relocate (FIFO,
splitting a lot when the move is partial — the ADR-0047 mechanic, every cost
field carried unchanged), and the conversion lands in the reconciliation log
under its own kind, superseding the standing shortfall flag at the origin slot.

A move is not an acquisition: aging, FIFO order, and carrying value never
change. Everything booked here is a reversing entry away from undone (ADR-0045)
— nothing edits or deletes a booked financial fact.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.application import entitlements as entitlements_app
from app.application.corporations import get_registered_corporation
from app.application.errors import MoveSuggestionNotFound, MoveSuggestionNotPending
from app.data.repositories import hangars as hangars_repo
from app.data.repositories import lots as lots_repo
from app.data.repositories import move_suggestions as moves_repo
from app.data.repositories import reconciliation as recon_repo
from app.domain.lots import OpenLot, plan_fifo


@dataclass(frozen=True)
class ConfirmMoveResult:
    """What the confirmation converted: the units reversed at the destination
    and relocated from the origin (equal by construction). Zero for the no-op
    double confirm, and when nothing was left to convert."""

    qty_moved: int


async def confirm_move(
    session: AsyncSession,
    *,
    corporation_eve_id: int,
    suggestion_id: uuid.UUID,
    confirmed_by_character_id: int,
    confirmed_by_name: str | None = None,
    now: datetime | None = None,
) -> ConfirmMoveResult:
    """Confirm one pending suggestion (ADR-0049, #201) — the happy path. Owns
    the commit; everything below is one unit of work.

    Simple guards:
    - A deemed lot partially consumed since the pairing converts only its
      `qty_remaining` (#204). The consumed units are spoken for and stay put:
      units SOLD keep their frozen deemed COGS on the booked sale rows (frozen
      facts, ADR-0043 — still flagged estimated); units TRANSFORMED (ADR-0047)
      carried their deemed cost into child lots, which keep it, still flagged
      estimated — no retroactive re-costing, no cascade into frozen sales.
    - The origin relocates at most what its idle lots still hold; both sides
      always convert the SAME quantity, so the books stay balanced.
    - A double confirm is a no-op (idempotent); a dismissed suggestion raises
      `MoveSuggestionNotPending` — see the error's docstring.

    The log entry is written at the ORIGIN slot: append-only, it supersedes the
    shortfall as that slot's latest event, which is what "resolving the flag"
    means in the ADR-0044 log (no standing flag anchors future pairings, and a
    fresh shortfall would log — and flag — anew)."""
    corp = await get_registered_corporation(session, corporation_eve_id)
    await entitlements_app.require_entitlement(
        session, corporation_id=corp.id, feature="accounting"
    )
    suggestion = await moves_repo.get_for_corp(
        session, corporation_id=corp.id, suggestion_id=suggestion_id
    )
    if suggestion is None:
        raise MoveSuggestionNotFound()
    if suggestion.status == "confirmed":
        return ConfirmMoveResult(qty_moved=0)
    if suggestion.status != "pending":
        raise MoveSuggestionNotPending()
    now = now or datetime.now(UTC)

    # How much is still convertible: the paired quantity, capped by what the
    # deemed lot still holds (see the partial-consumption guard above; a lot
    # deleted since — SET NULL — leaves nothing to reverse).
    deemed = (
        await lots_repo.get_for_corp(
            session, corporation_id=corp.id, lot_id=suggestion.excess_lot_id
        )
        if suggestion.excess_lot_id is not None
        else None
    )
    convertible = min(
        suggestion.qty, deemed.qty_remaining if deemed is not None else 0
    )

    # FIFO relocation plan over the origin's open lots of that type — capped
    # again by what actually sits there (plan_fifo reports the shortfall).
    origin_lots = await lots_repo.open_lots(
        session,
        corporation_id=corp.id,
        item_type_id=suggestion.type_id,
        location_id=suggestion.origin_location_id,
    )
    open_lots = [_open_lot(lot) for lot in origin_lots]
    plan = plan_fifo(open_lots, convertible)
    moved = convertible - plan.shortfall

    for consumption in plan.consumptions:
        await lots_repo.move_to_location(
            session,
            lot_id=consumption.lot_id,
            qty=consumption.qty,
            location_id=suggestion.destination_location_id,
        )
    if moved > 0 and deemed is not None:
        # The reversing entry: the deemed-cost units the excess pass invented
        # are consumed back out — the relocated lots now carry the destination's
        # stock at its real, measured cost basis.
        await lots_repo.consume(session, lot_id=deemed.id, qty=moved)

    await moves_repo.set_status(
        session, suggestion_id=suggestion.id, status="confirmed"
    )
    await recon_repo.add_event(
        session,
        corporation_id=corp.id,
        location_id=suggestion.origin_location_id,
        type_id=suggestion.type_id,
        kind="move_confirmed",
        qty=moved,
        occurred_at=now,
        note=await _confirmed_note(
            session,
            corp.id,
            destination_location_id=suggestion.destination_location_id,
            confirmed_by_character_id=confirmed_by_character_id,
            confirmed_by_name=confirmed_by_name,
        ),
    )
    await session.commit()
    return ConfirmMoveResult(qty_moved=moved)


def _open_lot(lot) -> OpenLot:
    return OpenLot(
        lot_id=lot.id,
        qty_remaining=lot.qty_remaining,
        acquired_at=lot.acquired_at,
    )


async def _confirmed_note(
    session: AsyncSession,
    corporation_id: uuid.UUID,
    *,
    destination_location_id: str,
    confirmed_by_character_id: int,
    confirmed_by_name: str | None,
) -> str:
    """Who confirmed, and where the stock went — the log's plain-English record
    of the conversion (the acting character, per the ADR: "the log shows who
    confirmed what"). Lowercase start: the UI splices it after a dash."""
    hangar_names = {
        h.location_id: h.location_name
        for h in await hangars_repo.list_for_corp(session, corporation_id)
    }
    destination = hangar_names.get(
        destination_location_id, destination_location_id
    )
    who = confirmed_by_name or f"character {confirmed_by_character_id}"
    return f"confirmed as a move to {destination} by {who}"
