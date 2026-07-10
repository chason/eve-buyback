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
from datetime import UTC, datetime
from decimal import Decimal

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
    # Instance app admin (ADR-0041): this character id must match the allowlist that
    # env.ts puts in BUYBACK_ADMIN_CHARACTER_IDS — if they drift, the admin journeys
    # fail loudly. Admin-ness is orthogonal to the corp role, so is_ceo stays False.
    "admin": {
        "character_id": 90000090,
        "character_name": "Site Operator",
        "corporation_id": SECOND_CORP["eve_id"],
        "corporation_name": SECOND_CORP["name"],
        "is_director": False,
        "is_ceo": False,
        "encrypted_login_token": None,
    },
    # A member of the SECOND corp: journeys that create records (e.g. the appraise
    # flow) run as this persona so corp scoping keeps the first corp's history empty
    # for the journeys that assert emptiness.
    "hauler": {
        "character_id": 90000003,
        "character_name": "Renn Okata",
        "corporation_id": SECOND_CORP["eve_id"],
        "corporation_name": SECOND_CORP["name"],
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


# Mini-SDE fixture (#171): just enough reference data to appraise a mineral paste with
# the default config (90% of Jita buy percentile) and ZERO network — the real SDE seed
# pulls from Fuzzwork, which e2e/CI must never do. Prices are chosen so journey math is
# exact: e.g. 1000 Tritanium @ 4.00 × 0.90 = 3,600 ISK.
#   (type_id, name, buy_percentile)  — all minerals: group 18, category 4, mg 1857.
MINERALS = [
    (34, "Tritanium", "4.00"),
    (35, "Pyerite", "8.00"),
    (36, "Mexallon", "50.00"),
    (37, "Isogen", "25.00"),
    (38, "Nocxium", "600.00"),
    (39, "Zydrine", "1500.00"),
    (40, "Megacyte", "3000.00"),
]
JITA = "60003760"  # the default market hub (ADR-0006)


async def _seed() -> None:
    # Imported here: app.data.db builds its engine from settings at import time, and we
    # want that to happen after the database exists.
    from app.data.db import SessionLocal, engine
    from app.data.models import MarketPrice, SdeMarketGroup, SdeType
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

        session.add(SdeMarketGroup(market_group_id=1857, parent_id=None, name="Minerals"))
        now = datetime.now(UTC)  # fresh within the market-cache TTL → no refetch
        for type_id, name, buy_percentile in MINERALS:
            session.add(
                SdeType(
                    type_id=type_id,
                    name=name,
                    group_id=18,
                    category_id=4,
                    market_group_id=1857,
                    volume=Decimal("0.01"),
                    portion_size=1,
                    published=True,
                )
            )
            buy = Decimal(buy_percentile)
            sell = buy * Decimal("1.25")
            session.add(
                MarketPrice(
                    hub_id=JITA,
                    type_id=type_id,
                    buy_weighted_average=buy,
                    buy_max=buy,
                    buy_min=buy / 2,
                    buy_median=buy,
                    buy_percentile=buy,
                    buy_volume=Decimal(1_000_000_000),
                    buy_order_count=100,
                    sell_weighted_average=sell,
                    sell_max=sell * 2,
                    sell_min=sell,
                    sell_median=sell,
                    sell_percentile=sell,
                    sell_volume=Decimal(1_000_000_000),
                    sell_order_count=100,
                    fetched_at=now,
                )
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
