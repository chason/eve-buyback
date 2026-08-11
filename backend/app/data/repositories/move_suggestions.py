"""Move-suggestion persistence (ADR-0049, #200): create the pairings the sync
proposed, skip duplicates of a still-pending pair, and read the pending list for
the "Needs a look" area. The application owns the commit."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import MoveSuggestion
from app.data.records import MoveSuggestionRecord


async def add(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    type_id: int,
    origin_location_id: str,
    destination_location_id: str,
    qty: int,
    shortfall_event_id: uuid.UUID,
    excess_lot_id: uuid.UUID,
) -> MoveSuggestionRecord:
    suggestion = MoveSuggestion(
        corporation_id=corporation_id,
        type_id=type_id,
        origin_location_id=origin_location_id,
        destination_location_id=destination_location_id,
        qty=qty,
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
    excess_lot_id: uuid.UUID,
) -> bool:
    """Whether this exact pair already has a pending suggestion — a later sync
    must never duplicate it (ADR-0049)."""
    row = await session.scalar(
        select(MoveSuggestion.id).where(
            MoveSuggestion.corporation_id == corporation_id,
            MoveSuggestion.shortfall_event_id == shortfall_event_id,
            MoveSuggestion.excess_lot_id == excess_lot_id,
            MoveSuggestion.status == "pending",
        )
    )
    return row is not None


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
