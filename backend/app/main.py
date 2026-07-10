import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from app._version import APP_VERSION
from app.application.errors import ApplicationError
from app.config import INSECURE_SESSION_SECRET, Settings, get_settings
from app.interface.errors import application_error_handler
from app.interface.jobs import (
    run_contracts_refresh,
    run_market_refresh,
    run_payments_reconcile,
    run_roster_refresh,
)
from app.interface.middleware import CsrfHeaderMiddleware
from app.interface.og import router as og_router
from app.interface.spa import SpaStaticFiles
from app.interface.v1 import api_router
from app.plugins.cache import build_cache

logger = logging.getLogger("app")


def _warn_on_insecure_defaults(settings: Settings) -> None:
    """Loudly flag insecure relaxations at boot. The model validators already
    refuse to start production with placeholder secrets (#25); this surfaces the
    development relaxation so it can't be mistaken for a hardened deployment."""
    if settings.environment != "development":
        return
    if settings.session_secret == INSECURE_SESSION_SECRET:
        logger.warning(
            "Running in DEVELOPMENT mode: using the publicly known placeholder "
            "session secret and non-Secure cookies. Never expose this to a public "
            "network. Set BUYBACK_ENVIRONMENT=production with real secrets to deploy."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    _warn_on_insecure_defaults(settings)
    # Shared async HTTP client for outbound EVE SSO / ESI / Fuzzwork calls (eve-esi skill).
    # `X-Compatibility-Date` pins ESI to a reviewed API behaviour (ESI versions by date, not
    # URL route); SSO/Fuzzwork ignore the unknown header, so it's safe to set globally here.
    app.state.http = httpx.AsyncClient(
        timeout=httpx.Timeout(20.0),
        headers={
            "User-Agent": f"{settings.app_name}/{app.version} (EVE buyback)",
            "X-Compatibility-Date": settings.esi_compatibility_date,
        },
    )
    # Process-wide cache backing the market-price L1 tier (ADR-0033).
    app.state.cache = build_cache(settings)
    # Process-wide ESI concurrency cap (ADR-0035): one semaphore shared by every
    # in-flight appraisal + the background refresh, so they can't multiply outbound
    # ESI market requests and exhaust ESI's per-IP error budget.
    app.state.esi_semaphore = asyncio.Semaphore(settings.esi_market_concurrency)
    # Periodic background jobs (ADR-0010): market-price refresh (ADR-0034) + daily corp
    # roster refresh (ADR-0036). One in-process scheduler; each job is added per its flag.
    app.state.scheduler = _start_scheduler(app, settings)
    yield
    # Guarded + ordered: a failure in one teardown step must not skip the others.
    if app.state.scheduler is not None:
        try:
            app.state.scheduler.shutdown(wait=False)
        except Exception:  # noqa: BLE001 — teardown is best-effort
            logger.exception("scheduler shutdown failed")
    try:
        await app.state.cache.aclose()
    except Exception:  # noqa: BLE001 — teardown is best-effort
        logger.exception("cache aclose failed during shutdown")
    finally:
        await app.state.http.aclose()


def _start_scheduler(
    app: FastAPI, settings: Settings
) -> AsyncIOScheduler | None:
    """Start the in-process scheduler (ADR-0010) with whichever background jobs are
    enabled — market-price refresh (ADR-0034), the daily corp-roster refresh (ADR-0036),
    the corp-contract watcher (ADR-0037), and/or payment reconciliation (ADR-0042). Each
    job runs first after a short delay so a cold deploy warms soon without hammering ESI
    at boot. Returns None when all are off."""
    if not (
        settings.market_background_refresh_enabled
        or settings.roster_background_refresh_enabled
        or settings.contracts_background_refresh_enabled
        or settings.payments_background_refresh_enabled
    ):
        return None
    scheduler = AsyncIOScheduler()
    if settings.market_background_refresh_enabled:
        scheduler.add_job(
            run_market_refresh,
            trigger=IntervalTrigger(seconds=settings.market_refresh_interval_seconds),
            args=[app],
            id="market_refresh",
            max_instances=1,  # never overlap a slow run with the next tick
            coalesce=True,  # collapse missed ticks into one
            next_run_time=datetime.now()
            + timedelta(seconds=settings.market_refresh_initial_delay_seconds),
        )
    if settings.roster_background_refresh_enabled:
        scheduler.add_job(
            run_roster_refresh,
            trigger=IntervalTrigger(seconds=settings.roster_refresh_interval_seconds),
            args=[app],
            id="roster_refresh",
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now()
            + timedelta(seconds=settings.roster_refresh_initial_delay_seconds),
        )
    if settings.contracts_background_refresh_enabled:
        scheduler.add_job(
            run_contracts_refresh,
            trigger=IntervalTrigger(
                seconds=settings.contracts_refresh_interval_seconds
            ),
            args=[app],
            id="contracts_refresh",
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now()
            + timedelta(seconds=settings.contracts_refresh_initial_delay_seconds),
        )
    if settings.payments_background_refresh_enabled:
        scheduler.add_job(
            run_payments_reconcile,
            trigger=IntervalTrigger(
                seconds=settings.payments_refresh_interval_seconds
            ),
            args=[app],
            id="payments_reconcile",
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now()
            + timedelta(seconds=settings.payments_refresh_initial_delay_seconds),
        )
    scheduler.start()
    return scheduler


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Buyback API", version=APP_VERSION, lifespan=lifespan)

    # Translate application-layer errors into HTTP responses (interface concern).
    app.add_exception_handler(ApplicationError, application_error_handler)

    # Require a custom header on state-changing API requests (ADR-0017).
    app.add_middleware(CsrfHeaderMiddleware)

    # Signed session cookie; stores identity only, never EVE tokens (ADR-0004).
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie=settings.session_cookie_name,
        same_site="lax",
        https_only=settings.session_https_only,
        max_age=settings.session_max_age,
    )

    app.include_router(api_router, prefix="/api/v1")

    # Server-rendered Open Graph tags for shared appraisal links (ADR-0040). Registered
    # before the SPA mount so /a/{public_id} is matched here (injecting <meta> tags) while
    # still serving the SPA shell for the browser to hydrate.
    app.include_router(og_router)

    # Production single-deployable: serve the built SPA under "/" (ADR-0012).
    # Mounted last so /api/v1 keeps priority. No-op in dev (no static_dir / dist),
    # where Vite serves the SPA and proxies /api.
    _mount_spa(app, settings)
    return app


def _mount_spa(app: FastAPI, settings: Settings) -> None:
    """Mount the compiled SPA when present, with index.html history fallback."""
    if not settings.static_dir:
        return
    static_dir = Path(settings.static_dir)
    if not static_dir.is_dir():
        return
    app.mount("/", SpaStaticFiles(directory=static_dir, html=True), name="spa")


app = create_app()
