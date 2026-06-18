"""Appraisal↔contract link persistence (ADR-0037). A mutable side-table refreshed by the
contract watcher; the appraisal itself is write-once (ADR-0014). Reads return records;
the application owns the commit."""

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import Appraisal, AppraisalContract
from app.data.records import AppraisalContractRecord
from app.domain.contracts import ContractLink


async def appraisal_public_id_to_id(
    session: AsyncSession, *, corporation_id: uuid.UUID
) -> dict[str, uuid.UUID]:
    """The corp's `{public_id: appraisal UUID}` map — for matching a contract's title
    (which carries an appraisal's public_id) back to the appraisal."""
    rows = (
        await session.execute(
            select(Appraisal.public_id, Appraisal.id).where(
                Appraisal.corporation_id == corporation_id
            )
        )
    ).all()
    return {public_id: appraisal_id for public_id, appraisal_id in rows}


async def reconcile_for_corp(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    links: Sequence[ContractLink],
) -> None:
    """Make the corp's `appraisal_contracts` rows match `links` (one per appraisal): upsert
    each desired link by `appraisal_id`, and delete the corp's rows whose appraisal is no
    longer linked (its contract vanished/cancelled with no better match)."""
    existing = {
        row.appraisal_id: row
        for row in (
            await session.execute(
                select(AppraisalContract).where(
                    AppraisalContract.corporation_id == corporation_id
                )
            )
        ).scalars()
    }
    desired = {link.appraisal_id for link in links}

    for link in links:
        row = existing.get(link.appraisal_id)
        if row is None:
            session.add(
                AppraisalContract(
                    appraisal_id=link.appraisal_id,
                    corporation_id=corporation_id,
                    contract_id=link.contract_id,
                    status=link.status,
                    issued_at=link.issued_at,
                    completed_at=link.completed_at,
                )
            )
        else:
            row.contract_id = link.contract_id
            row.status = link.status
            row.issued_at = link.issued_at
            row.completed_at = link.completed_at

    for appraisal_id, row in existing.items():
        if appraisal_id not in desired:
            await session.delete(row)


async def get_for_appraisal(
    session: AsyncSession, *, appraisal_id: uuid.UUID
) -> AppraisalContractRecord | None:
    row = (
        await session.execute(
            select(AppraisalContract).where(
                AppraisalContract.appraisal_id == appraisal_id
            )
        )
    ).scalar_one_or_none()
    return AppraisalContractRecord.model_validate(row) if row else None


async def get_matched_contract(
    session: AsyncSession, *, public_id: str, corporation_id: uuid.UUID
) -> AppraisalContractRecord | None:
    """The contract linked to the appraisal with this `public_id` **within the given corp**
    (ADR-0038). Corp-scoped by joining `appraisals` so one corp can't open another's
    contract; None when the appraisal is unknown/cross-corp or has no matched contract."""
    row = (
        await session.execute(
            select(AppraisalContract)
            .join(Appraisal, AppraisalContract.appraisal_id == Appraisal.id)
            .where(
                Appraisal.public_id == public_id,
                Appraisal.corporation_id == corporation_id,
            )
        )
    ).scalar_one_or_none()
    return AppraisalContractRecord.model_validate(row) if row else None
