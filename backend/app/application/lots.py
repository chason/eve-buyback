"""Lot-ledger use cases (ADR-0043).

Currently the ingestion half of #151: when the contract watcher (ADR-0037) confirms a
buyback contract completed, the appraisal's accepted lines become inventory lots with a
verified cost basis.

Ingestion is deliberately NOT gated by the accounting entitlement (ADR-0042): ESI only
surfaces recent contracts, so skipping unpaid corps would leave permanent holes in a
ledger they later pay to see. The paid gate stays on the read APIs.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.data.repositories import appraisals as appraisals_repo
from app.data.repositories import lots as lots_repo
from app.domain.contracts import ContractLink


async def materialize_buyback_lots(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    links: Sequence[ContractLink],
    now: datetime,
) -> int:
    """Create the lots for appraisals whose contract just completed (#151): one lot per
    accepted line at the exact price paid (`unit_price` — so `cost_is_estimated` stays
    False), sitting at the appraisal's delivery location, acquired when the contract
    completed. Idempotent per appraisal: `completed` is terminal for lot creation, so
    an appraisal that already has lots is never touched again, whatever the watcher
    later observes. Returns the number of lots created.

    Runs inside the watcher's unit of work — the caller owns the commit, so the link
    update and the lots it implies land atomically."""
    pending = [
        link
        for link in links
        if link.status == "completed"
        and not await lots_repo.exists_for_appraisal(session, link.appraisal_id)
    ]
    if not pending:
        return 0

    ids = [link.appraisal_id for link in pending]
    lines_by_appraisal = await appraisals_repo.accepted_lines_for_lots(session, ids)
    facts = await appraisals_repo.match_facts(session, ids)

    created = 0
    for link in pending:
        _, location_id = facts.get(link.appraisal_id, (None, None))
        for line in lines_by_appraisal.get(link.appraisal_id, []):
            await lots_repo.create_lot(
                session,
                corporation_id=corporation_id,
                item_type_id=line.type_id,
                qty=line.quantity,
                unit_purchase_cost=line.unit_price,
                acquired_at=link.completed_at or now,
                source="buyback",
                appraisal_id=link.appraisal_id,
                location_id=location_id,
            )
            created += 1
    return created
