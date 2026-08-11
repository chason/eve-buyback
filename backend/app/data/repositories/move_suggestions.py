"""Move-suggestion persistence (ADR-0049, #200/#202): create the pairings the
sync proposed, skip duplicates of a still-pending or already-dismissed pair,
read the pending list for the "Needs a look" area, and move a suggestion out of
`pending` (dismiss/withdraw/confirm). The application owns the commit."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import MoveSuggestion
from app.data.records import MoveSuggestionRecord
from app.domain.moves import MoveSuggestionStatus


async def add(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    type_id: int,
    origin_location_id: str,
    destination_location_id: str,
    qty: int,
    shortfall_event_id: uuid.UUID,
    excess_lot_id: uuid.UUID | None = None,
    qty_listed: int = 0,
    qty_sold: int = 0,
) -> MoveSuggestionRecord:
    suggestion = MoveSuggestion(
        corporation_id=corporation_id,
        type_id=type_id,
        origin_location_id=origin_location_id,
        destination_location_id=destination_location_id,
        qty=qty,
        qty_listed=qty_listed,
        qty_sold=qty_sold,
        shortfall_event_id=shortfall_event_id,
        excess_lot_id=excess_lot_id,
        status="pending",
    )
    session.add(suggestion)
    await session.flush()
    await session.refresh(suggestion)
    return MoveSuggestionRecord.model_validate(suggestion)


async def pending_exists(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    shortfall_event_id: uuid.UUID,
    destination_location_id: str,
) -> bool:
    """Whether this shortfall already has a pending pair toward this
    destination — a later sync must never duplicate it (ADR-0049). Keyed by the
    flag + destination, not the excess lot: sell-side pairs have no lot (#206),
    and the shortfall/destination slot is what the manager is deciding about."""
    row = await session.scalar(
        select(MoveSuggestion.id).where(
            MoveSuggestion.corporation_id == corporation_id,
            MoveSuggestion.shortfall_event_id == shortfall_event_id,
            MoveSuggestion.destination_location_id == destination_location_id,
            MoveSuggestion.status == "pending",
        )
    )
    return row is not None


async def dismissed_exists(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    shortfall_event_id: uuid.UUID,
    qty: int,
) -> bool:
    """Whether a human already dismissed this pattern — same standing shortfall
    flag, same paired quantity (ADR-0049, #202). The sync must not re-ask while
    nothing changed; a changed magnitude gets a new anchor event or a new qty
    and may legitimately suggest again. Only `dismissed` suppresses — a
    `withdrawn` suggestion was invalidated by the world, not decided by anyone."""
    row = await session.scalar(
        select(MoveSuggestion.id).where(
            MoveSuggestion.corporation_id == corporation_id,
            MoveSuggestion.shortfall_event_id == shortfall_event_id,
            MoveSuggestion.qty == qty,
            MoveSuggestion.status == "dismissed",
        )
    )
    return row is not None


async def get_for_corp(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    suggestion_id: uuid.UUID,
) -> MoveSuggestionRecord | None:
    """One suggestion, corp-scoped — None for another corp's suggestion as much
    as for a missing one, so cross-tenant probing is indistinguishable from
    absence (the `lots.get_for_corp` posture)."""
    row = (
        await session.execute(
            select(MoveSuggestion).where(
                MoveSuggestion.id == suggestion_id,
                MoveSuggestion.corporation_id == corporation_id,
            )
        )
    ).scalar_one_or_none()
    return MoveSuggestionRecord.model_validate(row) if row else None


async def set_status(
    session: AsyncSession,
    *,
    suggestion_id: uuid.UUID,
    status: MoveSuggestionStatus,
    qty_retired: int | None = None,
) -> None:
    """Advance a suggestion's lifecycle (pending → confirmed/dismissed/
    withdrawn). The application decides the transition rules; this only
    writes it. A confirmation passes `qty_retired` — what it actually retired
    for the sold portion (#207), the state that keeps retired quantity from
    re-pairing."""
    row = (
        await session.execute(
            select(MoveSuggestion).where(MoveSuggestion.id == suggestion_id)
        )
    ).scalar_one()
    row.status = status
    if qty_retired is not None:
        row.qty_retired = qty_retired
    await session.flush()


async def sold_retired_by_destination_type(
    session: AsyncSession, *, corporation_id: uuid.UUID
) -> dict[tuple[str, int], int]:
    """Quantity prior confirmations retired for already-sold evidence (#207),
    per `(destination_location_id, type_id)` — subtracted from the fresh
    estimated-cost sales so retired quantity never re-pairs (ADR-0049), and
    the offset a new confirmation's true-up walk skips."""
    rows = (
        await session.execute(
            select(
                MoveSuggestion.destination_location_id,
                MoveSuggestion.type_id,
                func.sum(MoveSuggestion.qty_retired),
            )
            .where(
                MoveSuggestion.corporation_id == corporation_id,
                MoveSuggestion.status == "confirmed",
                MoveSuggestion.qty_retired > 0,
            )
            .group_by(
                MoveSuggestion.destination_location_id, MoveSuggestion.type_id
            )
        )
    ).all()
    return {(loc, tid): int(qty) for loc, tid, qty in rows}


async def list_pending(
    session: AsyncSession, *, corporation_id: uuid.UUID
) -> list[MoveSuggestionRecord]:
    rows = (
        (
            await session.execute(
                select(MoveSuggestion)
                .where(
                    MoveSuggestion.corporation_id == corporation_id,
                    MoveSuggestion.status == "pending",
                )
                .order_by(MoveSuggestion.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [MoveSuggestionRecord.model_validate(row) for row in rows]
