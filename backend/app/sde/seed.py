"""Deploy-time SDE seed (ADR-0009). Run with:

    uv run python -m app.sde.seed              # always (re)seed
    uv run python -m app.sde.seed --if-needed  # seed only when the SDE is incomplete

Downloads Fuzzwork's SDE dumps (types, market groups, ore reprocessing yields, NPC
stations) and upserts the curated subset into the app database. Idempotent — safe to
re-run after each EVE expansion. The container entrypoint runs `--if-needed` on boot
so a fresh deploy self-seeds without re-downloading on every restart. Standalone: no
FastAPI app required.
"""

import argparse
import asyncio

import httpx

from app.application.sde import seed_if_needed, seed_reference_data
from app.config import get_settings
from app.data.db import SessionLocal
from app.plugins.sde_source import FUZZWORK_DUMP_BASE, SdeSource


async def main(*, if_needed: bool) -> None:
    settings = get_settings()
    user_agent = f"{settings.app_name}/seed (EVE buyback)"
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(120.0), headers={"User-Agent": user_agent}
    ) as http:
        source = SdeSource(http)
        async with SessionLocal() as session:
            if if_needed:
                metadata = await seed_if_needed(
                    session, source, source_label=FUZZWORK_DUMP_BASE
                )
            else:
                metadata = await seed_reference_data(
                    session, source, source_label=FUZZWORK_DUMP_BASE
                )

    if metadata is None:
        print("SDE already seeded; skipping.")
        return
    print(
        f"Seeded SDE from {metadata.source}: "
        f"{metadata.type_count} types, "
        f"{metadata.market_group_count} market groups "
        f"(imported_at={metadata.imported_at.isoformat()})"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed SDE reference data from Fuzzwork.")
    parser.add_argument(
        "--if-needed",
        action="store_true",
        help="seed only when the SDE is missing/incomplete (cheap no-op otherwise)",
    )
    args = parser.parse_args()
    asyncio.run(main(if_needed=args.if_needed))
