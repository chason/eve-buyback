import os

# Configure settings before the app (and its cached settings/engine) import.
os.environ.setdefault("BUYBACK_ENVIRONMENT", "development")
os.environ.setdefault("BUYBACK_EVE_CLIENT_ID", "test-client-id")
os.environ.setdefault("BUYBACK_EVE_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("BUYBACK_SESSION_SECRET", "test-session-secret")
# Force a dedicated test database, regardless of any .env value.
os.environ["BUYBACK_DATABASE_URL"] = "sqlite+aiosqlite:///./test_buyback.db"

import pytest_asyncio  # noqa: E402

from app import models  # noqa: E402,F401 -- populate Base.metadata
from app.db import Base, engine  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def reset_database():
    """Recreate the schema before each test for isolation."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
