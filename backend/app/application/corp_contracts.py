"""Corp contract watcher (ADR-0037).

Reads the corp's EVE item-exchange contracts with the persisted **Corp ESI access** token
(ADR-0029/0036), matches each to an appraisal by the appraisal `public_id` the member
pastes into the contract Description, and **validates** the contract genuinely matches the
appraisal (exact items, price, location). Each appraisal gets its current best status —
`in_progress`, `completed`, a void state (`rejected`/`cancelled`/`expired`/`failed`), or
`mismatch` when a contract cites the appraisal but doesn't match it.

Run off-request by the daily-ish background job (`interface/jobs.py`). Reuses the stored
token server-side; a missing contracts **scope or in-game role** yields a 403 that is
logged and skipped **without** flagging the token failed (it still works for structures and
the roster — mirrors the roster members-403 nuance, #68)."""

import logging
import re
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
    ContractLink,
    ContractStatus,
    contract_matches,
    derive_lifecycle_status,
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

# Maximal runs of the appraisal public-id alphabet (base64url) in a contract title.
_ID_RUN = re.compile(r"[A-Za-z0-9_-]+")
_PUBLIC_ID_LEN = 12  # generate_appraisal_id() → secrets.token_urlsafe(9)

# Lifecycle statuses worth item-validating (a live or accepted contract); voided ones are
# surfaced as-is without an items fetch.
_VALIDATABLE: frozenset[ContractStatus] = frozenset({"in_progress", "completed"})

# When several contracts match one appraisal, prefer the most meaningful (lower wins).
_PRIORITY: dict[ContractStatus, int] = {
    "in_progress": 0,
    "completed": 1,
    "mismatch": 2,
    "rejected": 3,
    "cancelled": 3,
    "expired": 3,
    "failed": 3,
}


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

    # Candidate (appraisal_id, contract, lifecycle) for every contract whose title cites
    # one of this corp's appraisals.
    candidates: list[tuple[uuid.UUID, CorporationContract, ContractStatus]] = []
    for c in contracts:
        lifecycle = derive_lifecycle_status(
            c.status, date_expired=c.date_expired, now=now
        )
        if lifecycle is None:
            continue
        appraisal_id = _match_appraisal(c.title, id_map)
        if appraisal_id is not None:
            candidates.append((appraisal_id, c, lifecycle))

    # Facts for the appraisals we need to validate (the live/accepted candidates).
    to_validate = {a for a, _, lc in candidates if lc in _VALIDATABLE}
    facts = await appraisals_repo.match_facts(session, list(to_validate))
    accepted = await appraisals_repo.accepted_line_items(session, list(to_validate))

    # Resolve each candidate's final status (mismatch when a validatable contract is off),
    # then keep the single best contract per appraisal.
    best: dict[uuid.UUID, ContractLink] = {}
    for appraisal_id, c, lifecycle in candidates:
        if lifecycle in _VALIDATABLE:
            items = await esi.get_corporation_contract_items(
                corporation_id, c.contract_id, access_token
            )
            accepted_total, location = facts.get(appraisal_id, (None, None))
            ok = accepted_total is not None and contract_matches(
                price=c.price,
                start_location_id=c.start_location_id,
                items=_included_items(items),
                accepted_total=accepted_total,
                delivery_location_id=location,
                accepted_items=accepted.get(appraisal_id, {}),
            )
            status: ContractStatus = lifecycle if ok else "mismatch"
        else:
            status = lifecycle

        link = ContractLink(
            appraisal_id=appraisal_id,
            contract_id=c.contract_id,
            status=status,
            issued_at=c.date_issued,
            completed_at=c.date_completed,
        )
        cur = best.get(appraisal_id)
        if cur is None or _is_better(link, cur):
            best[appraisal_id] = link

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


def _match_appraisal(
    title: str | None, id_map: dict[str, uuid.UUID]
) -> uuid.UUID | None:
    """The appraisal whose 12-char public_id appears in the contract title (case-sensitive,
    exact). Checks each base64url run and any 12-char window inside a longer run."""
    if not title:
        return None
    for run in _ID_RUN.findall(title):
        if run in id_map:
            return id_map[run]
        for i in range(len(run) - _PUBLIC_ID_LEN + 1):
            window = run[i : i + _PUBLIC_ID_LEN]
            if window in id_map:
                return id_map[window]
    return None


def _included_items(items: list[ContractItem]) -> dict[int, int]:
    """The items the issuer hands over (the buyback items), summed by type id."""
    out: dict[int, int] = {}
    for it in items:
        if it.is_included:
            out[it.type_id] = out.get(it.type_id, 0) + it.quantity
    return out


def _is_better(a: ContractLink, b: ContractLink) -> bool:
    """Prefer the more meaningful status; tiebreak the more recently issued contract."""
    pa, pb = _PRIORITY[a.status], _PRIORITY[b.status]
    if pa != pb:
        return pa < pb
    return a.issued_at > b.issued_at
