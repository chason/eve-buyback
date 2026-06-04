from fastapi import APIRouter
from sqlalchemy import text

from app.deps import SessionDep

router = APIRouter()


@router.get("/health")
async def health(session: SessionDep) -> dict[str, str]:
    """Liveness + DB connectivity check (proves the two halves talk)."""
    await session.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok"}
