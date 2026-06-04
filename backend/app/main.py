from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1 import api_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Startup hooks (market-refresh scheduler, etc.) attach here later.
    yield
    # Shutdown hooks.


def create_app() -> FastAPI:
    app = FastAPI(title="Buyback API", version="0.1.0", lifespan=lifespan)
    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
