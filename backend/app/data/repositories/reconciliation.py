"""Reconciliation-log persistence (ADR-0044): append events, read the recent list,
and the per-slot latest lookup the sync uses to avoid re-logging an unchanged
shortfall every run. The application owns the commit."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import ReconciliationEvent
from app.data.records import ReconciliationEventRecord
from app.domain.reconciliation import ReconciliationKind

# Log entries about a manager action (ADR-0049, #202/#208), not about the
# slot's stock: the sync's "already logged?" dedupe must look past them, or a
# dismissal — or a haul recorded/arrived at a slot with an unrelated standing
# shortfall — would make that shortfall look new again: spam re-flagged, and
# the anchor event id the pairing keys on silently replaced.
_MOVE_ANNOTATION_KINDS = (
    "move_dismissed",
    "move_withdrawn",
    "shipment_recorded",
    "shipment_arrived",
)


async def add_event(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    location_id: str,
    type_id: int,
    kind: ReconciliationKind,
    qty: int,
    occurred_at: datetime,
    unit_cost: Decimal | None = None,
    lot_id: uuid.UUID | None = None,
    flagged: bool = False,
    note: str | None = None,
) -> ReconciliationEventRecord:
    event = ReconciliationEvent(
        corporation_id=corporation_id,
        location_id=location_id,
        type_id=type_id,
        kind=kind,
        qty=qty,
        unit_cost=unit_cost,
        lot_id=lot_id,
        flagged=flagged,
        note=note,
        occurred_at=occurred_at,
    )
    session.add(event)
    await session.flush()
    await session.refresh(event)
    return ReconciliationEventRecord.model_validate(event)


async def list_for_corp(
    session: AsyncSession, *, corporation_id: uuid.UUID, limit: int = 50
) -> list[ReconciliationEventRecord]:
    rows = (
        (
            await session.execute(
                select(ReconciliationEvent)
                .where(ReconciliationEvent.corporation_id == corporation_id)
                .order_by(
                    ReconciliationEvent.occurred_at.desc(),
                    ReconciliationEvent.created_at.desc(),
                )
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [ReconciliationEventRecord.model_validate(row) for row in rows]


async def latest_by_slot(
    session: AsyncSession, *, corporation_id: uuid.UUID
) -> dict[tuple[str, int], ReconciliationEventRecord]:
    """The most recent event per `(location_id, type_id)` — the sync compares against
    this so an unchanged shortfall isn't re-logged every run (ADR-0044). Move
    annotations are skipped: they decorate a suggestion, not the slot's state."""
    rows = (
        (
            await session.execute(
                select(ReconciliationEvent)
                .where(
                    ReconciliationEvent.corporation_id == corporation_id,
                    ReconciliationEvent.kind.notin_(_MOVE_ANNOTATION_KINDS),
                )
                .order_by(
                    ReconciliationEvent.occurred_at,
                    ReconciliationEvent.created_at,
                )
            )
        )
        .scalars()
        .all()
    )
    latest: dict[tuple[str, int], ReconciliationEventRecord] = {}
    for row in rows:  # ascending — the last write per slot wins
        latest[(row.location_id, row.type_id)] = (
            ReconciliationEventRecord.model_validate(row)
        )
    return latest
