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

from app.application import market_refresh
from app.config import get_settings
from app.data.db import SessionLocal
from app.plugins.esi_market import EsiMarketClient
from app.plugins.sso import EveSsoClient
from app.plugins.token_cipher import TokenCipher

log = logging.getLogger(__name__)


async def run_market_refresh(app: FastAPI) -> None:
    """Scheduler entrypoint: refresh due non-Fuzzwork hub prices (ADR-0034). The use
    case already degrades per hub; this top-level guard keeps the recurring job alive
    across any unexpected failure."""
    settings = get_settings()
    esi_market = EsiMarketClient(app.state.http)
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
