"""E2E database + session bootstrap (ADR-0046). Runs inside the backend environment
(`uv run --directory backend`), with BUYBACK_DATABASE_URL already pointing at the
dedicated `buyback_e2e` database and BUYBACK_SESSION_SECRET at the e2e signing secret.

Two modes, because Playwright starts its webServer BEFORE globalSetup runs:

- `db`   — DROP + CREATE the e2e database, `alembic upgrade head` (the real migrations
           get smoke-tested too), seed deterministic fixture corps. Chained in front of
           uvicorn in the webServer command, so the database exists before the app boots.
- `mint` — print signed `buyback_session` cookies for the test personas (exactly as the
           app's own SessionMiddleware would: same itsdangerous signer, same secret) as
           one marker line the TypeScript global setup consumes. Needs no database.
"""

import argparse
import asyncio
import base64
import json
import os
import sys

# Run via `uv run --directory backend`, so CWD is backend/ — but this file lives in
# e2e/support/, so backend/ must be put on sys.path for `app` imports explicitly.
sys.path.insert(0, os.getcwd())

import asyncpg
from alembic import command
from alembic.config import Config
from itsdangerous import TimestampSigner
from sqlalchemy.engine import make_url

from app.config import get_settings

E2E_DB = "buyback_e2e"

# Deterministic test personas. `is_ceo` rides in the session cookie (ADR-0016), so the
# ceo persona resolves to the `ceo` role for the registered corp with no extra rows.
REGISTERED_CORP = {"eve_id": 98000001, "name": "Deep Space Ventures"}
SECOND_CORP = {"eve_id": 98000002, "name": "Jita Freight Collective"}
PERSONAS = {
    "ceo": {
        "character_id": 90000001,
        "character_name": "Aria Vance",
        "corporation_id": REGISTERED_CORP["eve_id"],
        "corporation_name": REGISTERED_CORP["name"],
        "is_director": False,
        "is_ceo": True,
        "encrypted_login_token": None,
    },
    "member": {
        "character_id": 90000002,
        "character_name": "Miko Ren",
        "corporation_id": REGISTERED_CORP["eve_id"],
        "corporation_name": REGISTERED_CORP["name"],
        "is_director": False,
        "is_ceo": False,
        "encrypted_login_token": None,
    },
}


async def _recreate_database() -> None:
    url = make_url(get_settings().database_url)
    assert url.database == E2E_DB, f"refusing to run against {url.database!r}"
    conn = await asyncpg.connect(
        host=url.host,
        port=url.port or 5432,
        user=url.username,
        password=url.password,
        database="postgres",
    )
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{E2E_DB}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{E2E_DB}"')
    finally:
        await conn.close()


async def _seed() -> None:
    # Imported here: app.data.db builds its engine from settings at import time, and we
    # want that to happen after the database exists.
    from app.data.db import SessionLocal, engine
    from app.data.repositories import corporations as corporations_repo

    async with SessionLocal() as session:
        for corp in (REGISTERED_CORP, SECOND_CORP):
            await corporations_repo.create_corporation(
                session,
                eve_corporation_id=corp["eve_id"],
                name=corp["name"],
                ceo_character_id=PERSONAS["ceo"]["character_id"],
                registered_by_character_id=PERSONAS["ceo"]["character_id"],
            )
        await session.commit()
    await engine.dispose()


def _mint_sessions() -> dict[str, str]:
    """Sign each persona's identity the way Starlette's SessionMiddleware does:
    base64(json) signed with itsdangerous.TimestampSigner(secret)."""
    signer = TimestampSigner(str(get_settings().session_secret))
    return {
        name: signer.sign(
            base64.b64encode(json.dumps({"user": identity}).encode())
        ).decode()
        for name, identity in PERSONAS.items()
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["db", "mint"])
    args = parser.parse_args()
    if args.mode == "db":
        asyncio.run(_recreate_database())
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_seed())
        print("e2e database rebuilt and seeded")
    else:
        print("E2E_SESSIONS_JSON=" + json.dumps(_mint_sessions()))


if __name__ == "__main__":
    main()
