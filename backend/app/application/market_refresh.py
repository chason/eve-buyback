"""Background market-price refresh (ADR-0034).

A use case run off-request by the scheduler (`interface/jobs.py`): it proactively
renews cached prices for every market hub whose data comes from **ESI, not Fuzzwork**
(non-Jita NPC stations and player structures), so appraisals at those hubs are served
warm instead of paying the slow ESI fetch on a cache miss.

Two refresh shapes (the user's choice):
- **ESI-region NPC hubs** — refresh the already-appraised *hot set* (`market_prices`
  rows for that hub) whose freshness is about to lapse. Region pricing is one request
  *per type*, so "all types" is intractable; the hot set is what people actually price.
- **Player structures** — the whole order book is a single fetch, so cache **every**
  type in it (full pre-warm), making even never-before-appraised items instant.

Degrades like the lazy path: a hub that errors (down ESI, revoked/denied structure
token) is logged and skipped; the others still refresh. Owns its unit of work — commits
per hub (via `persist_market_rows`), so partial progress survives a mid-cycle error.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.errors import (
    StructureEncryptionNotConfigured,
    StructureTokenExpired,
    StructureTokenMissing,
)
from app.application.market import persist_market_rows
from app.application.structure_tokens import get_structure_access_token
from app.config import Settings
from app.data.repositories import buyback_config as config_repo
from app.data.repositories import prices as prices_repo
from app.data.repositories import pricing_rules as rules_repo
from app.data.repositories import structure_tokens as tokens_repo
from app.domain.market import HubDescriptor, refresh_cutoff, resolve_market_source
from app.plugins.cache import Cache
from app.plugins.esi_market import EsiMarketClient, StructureAccessDenied
from app.plugins.sso import EveSsoClient
from app.plugins.token_cipher import TokenCipher

log = logging.getLogger(__name__)

# Token/access failures that mean "this hub can't be priced right now" — skip it and
# serve whatever cache exists, exactly as the lazy path does.
_SKIP_HUB_ERRORS = (
    StructureTokenMissing,
    StructureTokenExpired,
    StructureAccessDenied,
    StructureEncryptionNotConfigured,
    httpx.HTTPError,
)


@dataclass
class _HubGroup:
    """One distinct non-Fuzzwork hub and the corps that reference it (for structure
    tokens). `region_id` prefers any non-null value seen (SDE drift across saves)."""

    hub_id: str
    kind: str
    region_id: int | None
    corp_ids: list[uuid.UUID] = field(default_factory=list)


@dataclass(frozen=True)
class RefreshSummary:
    hubs_refreshed: int = 0
    types_written: int = 0


async def refresh_due_prices(
    session: AsyncSession,
    *,
    esi_market: EsiMarketClient,
    sso: EveSsoClient,
    cipher: TokenCipher,
    cache: Cache | None,
    settings: Settings,
    now: datetime,
) -> RefreshSummary:
    """Refresh every non-Fuzzwork hub whose cached prices are due (ADR-0034)."""
    cutoff = refresh_cutoff(
        now,
        ttl_seconds=settings.market_cache_ttl_seconds,
        interval_seconds=settings.market_refresh_interval_seconds,
    )
    l1_ttl = settings.market_l1_cache_ttl_seconds

    groups = await _gather_non_fuzzwork_hubs(session)
    hubs_refreshed = 0
    types_written = 0
    for group in groups.values():
        try:
            written = await _refresh_one_hub(
                session,
                group,
                esi_market=esi_market,
                sso=sso,
                cipher=cipher,
                cache=cache,
                now=now,
                cutoff=cutoff,
                l1_ttl=l1_ttl,
            )
        except _SKIP_HUB_ERRORS:
            log.warning(
                "background refresh skipped hub %s", group.hub_id, exc_info=True
            )
            continue
        except Exception:  # noqa: BLE001 — one hub must never abort the whole cycle
            log.exception("unexpected error refreshing hub %s", group.hub_id)
            continue
        if written:
            hubs_refreshed += 1
            types_written += written
    if hubs_refreshed:
        log.info(
            "background refresh: %d hub(s), %d price(s) renewed",
            hubs_refreshed,
            types_written,
        )
    return RefreshSummary(hubs_refreshed=hubs_refreshed, types_written=types_written)


async def _gather_non_fuzzwork_hubs(
    session: AsyncSession,
) -> dict[str, _HubGroup]:
    """All hubs configured anywhere (corp defaults + per-rule overrides) that price via
    ESI, grouped by hub_id with their referencing corps. Fuzzwork hubs are dropped."""
    configured = [
        *await config_repo.list_hubs(session),
        *await rules_repo.list_hub_overrides(session),
    ]
    groups: dict[str, _HubGroup] = {}
    for h in configured:
        hub = HubDescriptor(hub_id=h.hub_id, kind=h.kind, region_id=h.region_id)
        if resolve_market_source(hub) == "fuzzwork":
            continue
        group = groups.get(h.hub_id)
        if group is None:
            groups[h.hub_id] = group = _HubGroup(
                hub_id=h.hub_id, kind=h.kind, region_id=h.region_id
            )
        if group.region_id is None and h.region_id is not None:
            group.region_id = h.region_id  # prefer a known region over a missing one
        if h.corporation_id not in group.corp_ids:
            group.corp_ids.append(h.corporation_id)
    return groups


async def _refresh_one_hub(
    session: AsyncSession,
    group: _HubGroup,
    *,
    esi_market: EsiMarketClient,
    sso: EveSsoClient,
    cipher: TokenCipher,
    cache: Cache | None,
    now: datetime,
    cutoff: datetime,
    l1_ttl: int,
) -> int:
    """Refresh one hub if due; return the number of prices written (0 if nothing due
    or fetchable)."""
    hub = HubDescriptor(
        hub_id=group.hub_id, kind=group.kind, region_id=group.region_id
    )
    source = resolve_market_source(hub)

    if source == "esi_region":
        if group.region_id is None:
            log.warning(
                "hub %s has no region_id; cannot background-refresh via ESI",
                group.hub_id,
            )
            return 0
        due = await prices_repo.list_type_ids_for_hub(
            session, hub_id=group.hub_id, older_than=cutoff
        )
        if not due:
            return 0
        aggregates = await esi_market.get_region_aggregates(
            region_id=group.region_id, station_id=group.hub_id, type_ids=due
        )
        records = await persist_market_rows(
            session, cache, hub_id=group.hub_id, aggregates=aggregates, now=now,
            l1_ttl=l1_ttl,
        )
        return len(records)

    # esi_structure: the whole book dates from its freshest cached row; refresh the lot
    # once that's due (or when nothing is cached yet → pre-warm).
    latest = await prices_repo.latest_fetched_at(session, hub_id=group.hub_id)
    if latest is not None and latest >= cutoff:
        return 0
    aggregates = await _fetch_structure_book(
        session, esi_market=esi_market, sso=sso, cipher=cipher, group=group, now=now
    )
    if aggregates is None:
        return 0  # no referencing corp could fetch it (logged inside)
    records = await persist_market_rows(
        session, cache, hub_id=group.hub_id, aggregates=aggregates, now=now,
        l1_ttl=l1_ttl,
    )
    return len(records)


async def _fetch_structure_book(
    session: AsyncSession,
    *,
    esi_market: EsiMarketClient,
    sso: EveSsoClient,
    cipher: TokenCipher,
    group: _HubGroup,
    now: datetime,
) -> dict | None:
    """Fetch a structure's full order book using the first referencing corp whose token
    can actually read it (ADR-0034). Corps are tried healthiest-first — tokens not
    flagged `last_refresh_failed_at` before flagged ones, then least-recently-*used*
    first so the fetching token rotates across corps each cycle (#88; ordering in SQL).
    On success the winning corp's `last_used_at` is stamped so it falls to the back of
    the queue next time. Returns the aggregates, or None if no corp could fetch it."""
    for corp_id in await tokens_repo.list_corps_by_token_health(
        session, group.corp_ids
    ):
        try:
            access_token = await get_structure_access_token(
                session, sso, corporation_uuid=corp_id, cipher=cipher
            )
        except (
            StructureTokenMissing,
            StructureTokenExpired,
            StructureEncryptionNotConfigured,
        ):
            continue  # this corp's grant is unusable; try the next
        try:
            aggregates = await esi_market.get_all_structure_aggregates(
                structure_id=group.hub_id, access_token=access_token
            )
        except StructureAccessDenied:
            log.warning(
                "corp %s token denied access to structure %s; trying next corp",
                corp_id,
                group.hub_id,
            )
            continue
        # Stamp the winning token so selection rotates to another corp next cycle (#88).
        await tokens_repo.mark_used(session, corporation_id=corp_id, at=now)
        await session.commit()
        return aggregates
    log.warning(
        "no referencing corp could access structure %s; skipping", group.hub_id
    )
    return None
