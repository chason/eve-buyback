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
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.application import entitlements as entitlements_app
from app.application.corporations import get_registered_corporation
from app.application.errors import MoveSuggestionNotFound, MoveSuggestionNotPending
from app.data.records import MoveSuggestionRecord
from app.data.repositories import expenses as expenses_repo
from app.data.repositories import hangars as hangars_repo
from app.data.repositories import lots as lots_repo
from app.data.repositories import move_suggestions as moves_repo
from app.data.repositories import reconciliation as recon_repo
from app.data.repositories import sde as sde_repo
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
    haul_cost: Decimal | None = None,
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
    - An optional `haul_cost` (#205) books a hauling expense in the same UoW —
      a SELLING cost (ADR-0043/0045), never landed cost: the moved lots'
      carrying value must not change. Attribution: `LotExpense` carries one
      optional `lot_id`, and a move can relocate several lots, so the expense
      attaches to the relocated lot when exactly one moved and otherwise
      attributes through its note, which always names the move — allocating
      one ISK figure across lots would imply a precision it doesn't have.
      Omitted, zero, or a confirm that converted nothing books nothing.
    - Candidate origins (#203) are sibling pending suggestions sharing this
      one's deemed lot; the manager picks by confirming ONE of them — this
      function never chooses. A confirm that fully claims the lot withdraws
      the siblings (see `_withdraw_claimed_siblings`); a partial claim leaves
      them pending at their shrunken convertible quantity.

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

    relocated: list[uuid.UUID] = []
    for consumption in plan.consumptions:
        destination_lot = await lots_repo.move_to_location(
            session,
            lot_id=consumption.lot_id,
            qty=consumption.qty,
            location_id=suggestion.destination_location_id,
        )
        relocated.append(destination_lot.id)
    if moved > 0 and deemed is not None:
        # The reversing entry: the deemed-cost units the excess pass invented
        # are consumed back out — the relocated lots now carry the destination's
        # stock at its real, measured cost basis.
        await lots_repo.consume(session, lot_id=deemed.id, qty=moved)

    if haul_cost and moved > 0:
        # The freight the confirmer told us about (#205): a selling expense
        # attributed to the move — see the docstring for the attribution
        # stance. Booked in the same UoW, so a failed confirm books no cost.
        await expenses_repo.create_expense(
            session,
            corporation_id=corp.id,
            kind="hauling",
            amount=haul_cost,
            source="manual",
            incurred_at=now,
            lot_id=relocated[0] if len(relocated) == 1 else None,
            recorded_by_character_id=confirmed_by_character_id,
            note=await _haul_note(
                session,
                corp.id,
                type_id=suggestion.type_id,
                qty=moved,
                origin_location_id=suggestion.origin_location_id,
                destination_location_id=suggestion.destination_location_id,
            ),
        )

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
    if moved > 0 and deemed is not None:
        await _withdraw_claimed_siblings(
            session,
            corp.id,
            deemed_lot_id=deemed.id,
            remaining=deemed.qty_remaining - moved,
            chosen=suggestion,
            now=now,
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
    hangar_names = await _hangar_names(session, corporation_id)
    destination = hangar_names.get(
        destination_location_id, destination_location_id
    )
    who = confirmed_by_name or f"character {confirmed_by_character_id}"
    return f"confirmed as a move to {destination} by {who}"


async def _withdraw_claimed_siblings(
    session: AsyncSession,
    corporation_id: uuid.UUID,
    *,
    deemed_lot_id: uuid.UUID,
    remaining: int,
    chosen: MoveSuggestionRecord,
    now: datetime,
) -> None:
    """Candidate origins share one found-stock lot (#203): several pending
    suggestions can point at the same deemed lot, and confirming one claims its
    units. When the claim leaves nothing — the lot is fully reversed — the
    sibling candidates are withdrawn: bookkeeping, not a decision (#202's
    stance — no origin was ruled out, the stock is simply spoken for), logged
    in plain words at each sibling's origin slot. A partial claim leaves the
    siblings pending: the remainder is still real found stock, and a haul can
    arrive from two places — the cards just shrink to what is left (#204)."""
    if remaining > 0:
        return
    pending = await moves_repo.list_pending(session, corporation_id=corporation_id)
    siblings = [s for s in pending if _is_sibling(s, deemed_lot_id, chosen.id)]
    if not siblings:
        return
    hangar_names = await _hangar_names(session, corporation_id)
    origin = hangar_names.get(
        chosen.origin_location_id, chosen.origin_location_id
    )
    for sibling in siblings:
        await moves_repo.set_status(
            session, suggestion_id=sibling.id, status="withdrawn"
        )
        await recon_repo.add_event(
            session,
            corporation_id=corporation_id,
            location_id=sibling.origin_location_id,
            type_id=sibling.type_id,
            kind="move_withdrawn",
            qty=sibling.qty,
            occurred_at=now,
            note=f"The found stock was confirmed as a move from {origin}",
        )


def _is_sibling(
    s: MoveSuggestionRecord, deemed_lot_id: uuid.UUID, chosen_id: uuid.UUID
) -> bool:
    return s.excess_lot_id == deemed_lot_id and s.id != chosen_id


async def _haul_note(
    session: AsyncSession,
    corporation_id: uuid.UUID,
    *,
    type_id: int,
    qty: int,
    origin_location_id: str,
    destination_location_id: str,
) -> str:
    """The hauling expense's attribution (#205), in plain English: which move
    the cost belongs to, named the way the confirm card named it."""
    types = await sde_repo.get_types(session, [type_id])
    item = types[type_id].name if type_id in types else f"type {type_id}"
    hangar_names = await _hangar_names(session, corporation_id)
    origin = hangar_names.get(origin_location_id, origin_location_id)
    destination = hangar_names.get(
        destination_location_id, destination_location_id
    )
    return f"Hauling for the move of {qty} {item} from {origin} to {destination}"


async def _hangar_names(
    session: AsyncSession, corporation_id: uuid.UUID
) -> dict[str, str]:
    hangars = await hangars_repo.list_for_corp(session, corporation_id)
    return {h.location_id: h.location_name for h in hangars}
