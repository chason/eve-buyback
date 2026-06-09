from fastapi import APIRouter
from sqlalchemy import text

from app._version import APP_VERSION
from app.interface.deps import SessionDep

router = APIRouter()


@router.get("/health")
async def health(session: SessionDep) -> dict[str, str]:
    """Liveness + DB connectivity check (proves the two halves talk)."""
    await session.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok"}


@router.get("/version")
async def version() -> dict[str, str]:
    """The app version (bumped per merged PR). Public — shown in the UI footer."""
    return {"version": APP_VERSION}
