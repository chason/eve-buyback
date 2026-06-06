from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from app.api.v1 import api_router
from app.config import get_settings
from app.middleware import CsrfHeaderMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # Shared async HTTP client for outbound EVE SSO / ESI calls (eve-esi skill).
    app.state.http = httpx.AsyncClient(
        timeout=httpx.Timeout(20.0),
        headers={"User-Agent": f"{settings.app_name}/{app.version} (EVE buyback)"},
    )
    yield
    await app.state.http.aclose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Buyback API", version="0.1.0", lifespan=lifespan)

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
    return app


app = create_app()
