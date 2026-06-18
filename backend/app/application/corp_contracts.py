"""Corp contract watcher (ADR-0037).

Reads the corp's EVE item-exchange contracts with the persisted **Corp ESI access** token
(ADR-0029/0036), matches each to an appraisal by the appraisal `public_id` the member
pastes into the contract Description, and **validates** the contract genuinely matches the
appraisal (exact items, price, location). Each appraisal gets its current best status —
`in_progress`, `completed`, a void state (`rejected`/`cancelled`/`expired`/`failed`), or
`mismatch` when a contract cites the appraisal but doesn't match it.

This use case is pure orchestration: it acquires the token, fetches contracts + items, maps
them into the plain `ContractObservation`/`AppraisalFacts` the pure resolver in
`domain/contracts.py` consumes, and persists the result. The matching/priority/validation
rules all live in the domain layer.

Run off-request by the background job (`interface/jobs.py`). Reuses the stored token
server-side; a missing contracts **scope or in-game role** yields a 403 that is logged and
skipped **without** flagging the token failed (it still works for structures and the roster
— mirrors the roster members-403 nuance, #68)."""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.application import corp_esi_token as corp_esi_token_app
from app.application.corporations import get_registered_corporation
from app.application.errors import (
    CorpEsiTokenExpired,
    CorpEsiTokenMissing,
    StructureEncryptionNotConfigured,
)
from app.data.repositories import appraisal_contracts as links_repo
from app.data.repositories import appraisals as appraisals_repo
from app.domain.contracts import (
    VALIDATABLE_STATUSES,
    AppraisalFacts,
    ContractObservation,
    ContractStatus,
    derive_lifecycle_status,
    match_appraisal_id,
    resolve_best_links,
)
from app.plugins.esi import (
    ContractItem,
    CorporationContract,
    CorporationContractsForbidden,
    EsiClient,
)
from app.plugins.sso import EveSsoClient
from app.plugins.token_cipher import TokenCipher

log = logging.getLogger(__name__)

# A candidate contract: the appraisal it cites, the raw ESI contract, and its lifecycle.
_Candidate = tuple[uuid.UUID, CorporationContract, ContractStatus]


async def refresh_contracts(
    session: AsyncSession,
    sso: EveSsoClient,
    esi: EsiClient,
    *,
    corporation_id: int,
    cipher: TokenCipher,
    now: datetime | None = None,
) -> None:
    """Sync one corp's appraisal↔contract links from its current ESI contracts."""
    corp = await get_registered_corporation(session, corporation_id)
    now = now or datetime.now(UTC)

    try:
        access_token = await corp_esi_token_app.get_corp_esi_access_token(
            session, sso, corporation_uuid=corp.id, cipher=cipher
        )
    except (
        CorpEsiTokenMissing,
        CorpEsiTokenExpired,
        StructureEncryptionNotConfigured,
    ):
        return  # no usable token; token-health is owned where these are raised

    try:
        contracts = await esi.get_corporation_contracts(corporation_id, access_token)
    except CorporationContractsForbidden:
        # Old grant without the contracts scope, or the character lacks the role. Skip —
        # do NOT flag the token failed (it's a scope/role issue, not a refresh failure, #68).
        log.warning(
            "contracts scope/role missing for corp %s; skipping contract sync",
            corporation_id,
        )
        return

    id_map = await links_repo.appraisal_public_id_to_id(
        session, corporation_id=corp.id
    )

    # Every contract whose title cites one of this corp's appraisals, with its lifecycle.
    candidates: list[_Candidate] = []
    for c in contracts:
        lifecycle = derive_lifecycle_status(
            c.status, date_expired=c.date_expired, now=now
        )
        if lifecycle is None:
            continue
        appraisal_id = match_appraisal_id(c.title, id_map)
        if appraisal_id is not None:
            candidates.append((appraisal_id, c, lifecycle))

    facts = await _appraisal_facts(session, candidates)
    observations = [
        await _observe(esi, corporation_id, access_token, appraisal_id, c, lifecycle)
        for appraisal_id, c, lifecycle in candidates
    ]

    best = resolve_best_links(observations, facts)
    await links_repo.reconcile_for_corp(
        session, corporation_id=corp.id, links=list(best.values()), now=now
    )
    await session.commit()
    if best:
        log.info(
            "contract sync for corp %s: %d appraisal(s) linked",
            corporation_id,
            len(best),
        )


async def _appraisal_facts(
    session: AsyncSession, candidates: list[_Candidate]
) -> dict[uuid.UUID, AppraisalFacts]:
    """Load the price/location/accepted-items each validatable candidate must match."""
    to_validate = {a for a, _, lc in candidates if lc in VALIDATABLE_STATUSES}
    if not to_validate:
        return {}
    ids = list(to_validate)
    match_facts = await appraisals_repo.match_facts(session, ids)
    accepted = await appraisals_repo.accepted_line_items(session, ids)
    return {
        aid: AppraisalFacts(
            accepted_total=total,
            delivery_location_id=location,
            accepted_items=accepted.get(aid, {}),
        )
        for aid, (total, location) in match_facts.items()
    }


async def _observe(
    esi: EsiClient,
    corporation_id: int,
    access_token: str,
    appraisal_id: uuid.UUID,
    c: CorporationContract,
    lifecycle: ContractStatus,
) -> ContractObservation:
    """Map an ESI contract into the plain observation the resolver consumes — fetching its
    items only when the lifecycle is validatable (voided contracts aren't item-checked)."""
    items = (
        _included_items(
            await esi.get_corporation_contract_items(
                corporation_id, c.contract_id, access_token
            )
        )
        if lifecycle in VALIDATABLE_STATUSES
        else {}
    )
    return ContractObservation(
        appraisal_id=appraisal_id,
        contract_id=c.contract_id,
        lifecycle=lifecycle,
        issued_at=c.date_issued,
        completed_at=c.date_completed,
        price=c.price,
        start_location_id=c.start_location_id,
        items=items,
    )


def _included_items(items: list[ContractItem]) -> dict[int, int]:
    """The items the issuer hands over (the buyback items), summed by type id."""
    out: dict[int, int] = {}
    for it in items:
        if it.is_included:
            out[it.type_id] = out.get(it.type_id, 0) + it.quantity
    return out
