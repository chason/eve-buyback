"""Transformation-event persistence (ADR-0047): the audit record that a reprocess
occurred. The child lots point back via `lots.source_lot_id`; the application owns
the commit."""

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import LotTransformation


async def create_transformation(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    source_lot_id: uuid.UUID,
    qty_consumed: int,
    occurred_at: datetime,
    recorded_by_character_id: int | None = None,
    note: str | None = None,
) -> uuid.UUID:
    event = LotTransformation(
        corporation_id=corporation_id,
        source_lot_id=source_lot_id,
        qty_consumed=qty_consumed,
        occurred_at=occurred_at,
        recorded_by_character_id=recorded_by_character_id,
        note=note,
    )
    session.add(event)
    await session.flush()
    return event.id
