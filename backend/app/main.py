import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from app._version import APP_VERSION
from app.application.errors import ApplicationError
from app.config import INSECURE_SESSION_SECRET, Settings, get_settings
from app.interface.errors import application_error_handler
from app.interface.middleware import CsrfHeaderMiddleware
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
    # Shared async HTTP client for outbound EVE SSO / ESI calls (eve-esi skill).
    app.state.http = httpx.AsyncClient(
        timeout=httpx.Timeout(20.0),
        headers={"User-Agent": f"{settings.app_name}/{app.version} (EVE buyback)"},
    )
    # Process-wide cache backing the market-price L1 tier (ADR-0033).
    app.state.cache = build_cache(settings)
    yield
    await app.state.cache.aclose()
    await app.state.http.aclose()


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
