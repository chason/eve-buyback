import os

from sqlalchemy.engine import make_url

# Configure settings before the app (and its cached settings/engine) import.
os.environ.setdefault("BUYBACK_ENVIRONMENT", "development")
os.environ.setdefault("BUYBACK_EVE_CLIENT_ID", "test-client-id")
os.environ.setdefault("BUYBACK_EVE_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("BUYBACK_SESSION_SECRET", "test-session-secret")

# Run against a dedicated `<name>_test` database derived from the configured URL, so
# the suite (which drops/recreates the schema per test) never touches dev data.
# Requires a PostgreSQL BUYBACK_DATABASE_URL (asyncpg), normally from backend/.env.
from app.config import Settings, get_settings  # noqa: E402

_configured = make_url(Settings().database_url)
if _configured.drivername != "postgresql+asyncpg":
    raise RuntimeError(
        "Tests require a PostgreSQL BUYBACK_DATABASE_URL "
        f"(postgresql+asyncpg://...), got {_configured.drivername!r}. "
        "Set it in backend/.env and create the <name>_test database."
    )
_test_url = _configured.set(database=f"{_configured.database}_test")
os.environ["BUYBACK_DATABASE_URL"] = _test_url.render_as_string(hide_password=False)
get_settings.cache_clear()

import pytest_asyncio  # noqa: E402

import app.data.models  # noqa: E402,F401 -- populate Base.metadata
from app.data.db import Base, engine  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def reset_database():
    """Recreate the schema before each test for isolation."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    # Dispose the pool on *this* test's event loop before pytest tears it down —
    # otherwise asyncpg connections outlive their loop and the next test crashes.
    await engine.dispose()
