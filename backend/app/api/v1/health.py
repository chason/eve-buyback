from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session

router = APIRouter()

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/health")
async def health(session: SessionDep) -> dict[str, str]:
    """Liveness + DB connectivity check (proves the two halves talk)."""
    await session.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok"}
