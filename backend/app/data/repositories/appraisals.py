"""Appraisal persistence (ADR-0014). Write-once snapshots; reads return records.
The application owns commit."""

import uuid
from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import (
    Appraisal,
    AppraisalContract,
    AppraisalLine,
    Character,
)
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
    delivery_location_id: str | None = None,
    delivery_location_name: str | None = None,
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
        delivery_location_id=delivery_location_id,
        delivery_location_name=delivery_location_name,
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

# Matched-contract status (ADR-0037), LEFT-joined so no-contract appraisals survive.
_CONTRACT_STATUS = AppraisalContract.status.label("contract_status")
_CONTRACT_JOIN = (AppraisalContract, AppraisalContract.appraisal_id == Appraisal.id)

# History sort (ADR-0037): needs-attention first (in_progress, then mismatch), then
# completed, then the voided states, then no contract (NULL). Newest first within a bucket.
_STATUS_RANK = case(
    (AppraisalContract.status == "in_progress", 0),
    (AppraisalContract.status == "mismatch", 1),
    (AppraisalContract.status == "completed", 2),
    (AppraisalContract.status.is_(None), 4),
    else_=3,  # rejected / cancelled / expired / failed
)


async def get_by_public_id(
    session: AsyncSession, public_id: str
) -> AppraisalRecord | None:
    row = (
        await session.execute(
            select(Appraisal, _CREATOR_NAME, _CONTRACT_STATUS)
            .outerjoin(*_CREATOR_JOIN)
            .outerjoin(*_CONTRACT_JOIN)
            .where(Appraisal.public_id == public_id)
        )
    ).first()
    if row is None:
        return None
    appraisal, creator_name, contract_status = row
    lines = (
        await session.execute(
            select(AppraisalLine)
            .where(AppraisalLine.appraisal_id == appraisal.id)
            .order_by(AppraisalLine.position)
        )
    ).scalars().all()
    return _to_record(appraisal, lines, creator_name, contract_status)


async def accepted_line_items(
    session: AsyncSession, appraisal_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, dict[int, int]]:
    """For each appraisal, the `{type_id: total quantity}` of its **accepted** lines — the
    items the member should put in the contract — for contract-match validation (ADR-0037).
    Rejected lines and unresolved names (null type_id) are excluded."""
    if not appraisal_ids:
        return {}
    rows = (
        await session.execute(
            select(
                AppraisalLine.appraisal_id,
                AppraisalLine.type_id,
                func.sum(AppraisalLine.quantity),
            )
            .where(
                AppraisalLine.appraisal_id.in_(appraisal_ids),
                AppraisalLine.status == "accepted",
                AppraisalLine.type_id.is_not(None),
            )
            .group_by(AppraisalLine.appraisal_id, AppraisalLine.type_id)
        )
    ).all()
    out: dict[uuid.UUID, dict[int, int]] = {}
    for appraisal_id, type_id, qty in rows:
        out.setdefault(appraisal_id, {})[type_id] = int(qty)
    return out


async def match_facts(
    session: AsyncSession, appraisal_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, tuple[Decimal, str | None]]:
    """Per appraisal, `(accepted_total, delivery_location_id)` — the price and location a
    contract must match (ADR-0037)."""
    if not appraisal_ids:
        return {}
    rows = (
        await session.execute(
            select(
                Appraisal.id,
                Appraisal.accepted_total,
                Appraisal.delivery_location_id,
            ).where(Appraisal.id.in_(appraisal_ids))
        )
    ).all()
    return {aid: (total, loc) for aid, total, loc in rows}


# History lists hide zero-value appraisals (#31): a curiosity "what's this worth"
# click that prices to nothing (no accepted items) is still saved as a record (ADR-0014)
# and stays retrievable by its link, but it doesn't clutter the member's or the corp's
# history. An appraisal that bought back anything has accepted_total > 0.
_HAS_VALUE = Appraisal.accepted_total > 0


async def list_for_corp(
    session: AsyncSession, corporation_id: uuid.UUID
) -> list[AppraisalSummaryRecord]:
    stmt = (
        select(Appraisal, _CREATOR_NAME, _CONTRACT_STATUS)
        .outerjoin(*_CREATOR_JOIN)
        .outerjoin(*_CONTRACT_JOIN)
        .where(Appraisal.corporation_id == corporation_id, _HAS_VALUE)
        .order_by(_STATUS_RANK, Appraisal.created_at.desc())
    )
    rows = (await session.execute(stmt)).all()
    return [_to_summary(a, name, status) for a, name, status in rows]


async def list_for_character(
    session: AsyncSession, corporation_id: uuid.UUID, character_id: int
) -> list[AppraisalSummaryRecord]:
    """`character_id` is the EVE id of the creator (an audit field, not a FK)."""
    stmt = (
        select(Appraisal, _CREATOR_NAME, _CONTRACT_STATUS)
        .outerjoin(*_CREATOR_JOIN)
        .outerjoin(*_CONTRACT_JOIN)
        .where(
            Appraisal.corporation_id == corporation_id,
            Appraisal.created_by_character_id == character_id,
            _HAS_VALUE,
        )
        .order_by(_STATUS_RANK, Appraisal.created_at.desc())
    )
    rows = (await session.execute(stmt)).all()
    return [_to_summary(a, name, status) for a, name, status in rows]


def _to_summary(
    appraisal: Appraisal, creator_name: str | None, contract_status: str | None = None
) -> AppraisalSummaryRecord:
    return AppraisalSummaryRecord(
        public_id=appraisal.public_id,
        created_by_character_id=appraisal.created_by_character_id,
        created_by_character_name=creator_name,
        created_at=appraisal.created_at,
        market_hub_id=appraisal.market_hub_id,
        accepted_total=appraisal.accepted_total,
        rejected_count=appraisal.rejected_count,
        delivery_location_id=appraisal.delivery_location_id,
        delivery_location_name=appraisal.delivery_location_name,
        contract_status=contract_status,
    )


def _to_record(
    appraisal: Appraisal,
    lines: Sequence[AppraisalLine],
    creator_name: str | None,
    contract_status: str | None = None,
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
        delivery_location_id=appraisal.delivery_location_id,
        delivery_location_name=appraisal.delivery_location_name,
        lines=[AppraisalLineRecord.model_validate(line) for line in lines],
        contract_status=contract_status,
    )
