"""Appraisal persistence (ADR-0014). Write-once snapshots; reads return records.
The application owns commit."""

import uuid
from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import Appraisal, AppraisalLine, Character
from app.data.records import (
    AppraisalLineRecord,
    AppraisalRecord,
    AppraisalSummaryRecord,
)


async def create_appraisal(
    session: AsyncSession,
    *,
    public_id: str,
    corporation_id: uuid.UUID,
    created_by_character_id: int,
    created_by_character_name: str | None = None,
    market_hub_id: str,
    accepted_total: Decimal,
    rejected_count: int,
    request_json: dict,
    lines: Sequence[dict],
) -> AppraisalRecord:
    appraisal = Appraisal(
        public_id=public_id,
        corporation_id=corporation_id,
        created_by_character_id=created_by_character_id,
        market_hub_id=market_hub_id,
        accepted_total=accepted_total,
        rejected_count=rejected_count,
        request_json=request_json,
    )
    session.add(appraisal)
    await session.flush()  # assign appraisal.id

    line_objs = [
        AppraisalLine(appraisal_id=appraisal.id, position=i, **line)
        for i, line in enumerate(lines)
    ]
    session.add_all(line_objs)
    await session.flush()
    await session.refresh(appraisal)  # load server-default created_at

    return _to_record(appraisal, line_objs, created_by_character_name)


# Creator name is denormalized for display: created_by_character_id is an EVE-id
# audit field, so we LEFT JOIN the characters table on it (the creator is a
# logged-in character, so the row exists; None-safe if it somehow doesn't).
_CREATOR_NAME = Character.name.label("creator_name")
_CREATOR_JOIN = (Character, Character.eve_id == Appraisal.created_by_character_id)


async def get_by_public_id(
    session: AsyncSession, public_id: str
) -> AppraisalRecord | None:
    row = (
        await session.execute(
            select(Appraisal, _CREATOR_NAME)
            .outerjoin(*_CREATOR_JOIN)
            .where(Appraisal.public_id == public_id)
        )
    ).first()
    if row is None:
        return None
    appraisal, creator_name = row
    lines = (
        await session.execute(
            select(AppraisalLine)
            .where(AppraisalLine.appraisal_id == appraisal.id)
            .order_by(AppraisalLine.position)
        )
    ).scalars().all()
    return _to_record(appraisal, lines, creator_name)


async def list_for_corp(
    session: AsyncSession, corporation_id: uuid.UUID
) -> list[AppraisalSummaryRecord]:
    stmt = (
        select(Appraisal, _CREATOR_NAME)
        .outerjoin(*_CREATOR_JOIN)
        .where(Appraisal.corporation_id == corporation_id)
        .order_by(Appraisal.created_at.desc())
    )
    rows = (await session.execute(stmt)).all()
    return [_to_summary(a, name) for a, name in rows]


async def list_for_character(
    session: AsyncSession, corporation_id: uuid.UUID, character_id: int
) -> list[AppraisalSummaryRecord]:
    """`character_id` is the EVE id of the creator (an audit field, not a FK)."""
    stmt = (
        select(Appraisal, _CREATOR_NAME)
        .outerjoin(*_CREATOR_JOIN)
        .where(
            Appraisal.corporation_id == corporation_id,
            Appraisal.created_by_character_id == character_id,
        )
        .order_by(Appraisal.created_at.desc())
    )
    rows = (await session.execute(stmt)).all()
    return [_to_summary(a, name) for a, name in rows]


def _to_summary(
    appraisal: Appraisal, creator_name: str | None
) -> AppraisalSummaryRecord:
    return AppraisalSummaryRecord(
        public_id=appraisal.public_id,
        created_by_character_id=appraisal.created_by_character_id,
        created_by_character_name=creator_name,
        created_at=appraisal.created_at,
        market_hub_id=appraisal.market_hub_id,
        accepted_total=appraisal.accepted_total,
        rejected_count=appraisal.rejected_count,
    )


def _to_record(
    appraisal: Appraisal,
    lines: Sequence[AppraisalLine],
    creator_name: str | None,
) -> AppraisalRecord:
    return AppraisalRecord(
        public_id=appraisal.public_id,
        corporation_id=appraisal.corporation_id,
        created_by_character_id=appraisal.created_by_character_id,
        created_by_character_name=creator_name,
        created_at=appraisal.created_at,
        market_hub_id=appraisal.market_hub_id,
        accepted_total=appraisal.accepted_total,
        rejected_count=appraisal.rejected_count,
        lines=[AppraisalLineRecord.model_validate(line) for line in lines],
    )
