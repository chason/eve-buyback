"""Background-job wiring (ADR-0034).

The interface-layer adapter for the scheduled market refresh: it opens a DB session,
constructs the plugin clients from app state + settings (the same wiring the request
dependencies do), and invokes the application use case. Like a router it holds no
business logic — it assembles dependencies and calls the use case — and it must never
raise: a scheduler job that propagates an exception logs noisily and can stop firing.
"""

import logging
from datetime import UTC, datetime

from fastapi import FastAPI

from app.application import corp_roster, market_refresh
from app.config import get_settings
from app.data.db import SessionLocal
from app.data.repositories import corp_esi_token as tokens_repo
from app.plugins.esi import EsiClient
from app.plugins.esi_market import EsiMarketClient
from app.plugins.sso import EveSsoClient
from app.plugins.token_cipher import TokenCipher

log = logging.getLogger(__name__)


async def run_market_refresh(app: FastAPI) -> None:
    """Scheduler entrypoint: refresh due non-Fuzzwork hub prices (ADR-0034). The use
    case already degrades per hub; this top-level guard keeps the recurring job alive
    across any unexpected failure."""
    settings = get_settings()
    esi_market = EsiMarketClient(app.state.http, app.state.esi_semaphore)
    sso = EveSsoClient(settings, app.state.http)
    cipher = TokenCipher(settings.token_encryption_key)
    try:
        async with SessionLocal() as session:
            await market_refresh.refresh_due_prices(
                session,
                esi_market=esi_market,
                sso=sso,
                cipher=cipher,
                cache=app.state.cache,
                settings=settings,
                now=datetime.now(UTC),
            )
    except Exception:  # noqa: BLE001 — a recurring job must survive any failure
        log.exception("market refresh job failed")


async def run_roster_refresh(app: FastAPI) -> None:
    """Scheduler entrypoint (ADR-0036): re-pull every token-holding corp's member roster
    so the manager-designation picker stays current without anyone clicking. Each corp runs
    in its own session and try/except — one corp's revoked or no-roster-access token never
    aborts the cycle — and the manual cooldown is bypassed."""
    settings = get_settings()
    esi = EsiClient(app.state.http)
    sso = EveSsoClient(settings, app.state.http)
    cipher = TokenCipher(settings.token_encryption_key)
    try:
        async with SessionLocal() as session:
            corp_eve_ids = await tokens_repo.list_corp_eve_ids_with_token(session)
    except Exception:  # noqa: BLE001 — a recurring job must survive any failure
        log.exception("roster refresh job: listing token-holding corps failed")
        return
    for corp_eve_id in corp_eve_ids:
        try:
            async with SessionLocal() as session:
                await corp_roster.refresh_roster(
                    session,
                    sso,
                    esi,
                    corporation_id=corp_eve_id,
                    cipher=cipher,
                    now=datetime.now(UTC),
                    enforce_cooldown=False,
                )
        except Exception:  # noqa: BLE001 — one corp's failure must not abort the cycle
            log.warning(
                "roster refresh failed for corp %s", corp_eve_id, exc_info=True
            )
