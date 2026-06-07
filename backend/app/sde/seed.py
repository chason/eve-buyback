"""Deploy-time SDE seed (ADR-0009). Run with:

    uv run python -m app.sde.seed

Downloads Fuzzwork's invTypes / invMarketGroups dumps and upserts the curated
subset into the app database. Idempotent — safe to re-run after each EVE
expansion. Standalone: no FastAPI app required.
"""

import asyncio

import httpx

from app.application.sde import seed_reference_data
from app.config import get_settings
from app.data.db import SessionLocal
from app.plugins.sde_source import FUZZWORK_DUMP_BASE, SdeSource


async def main() -> None:
    settings = get_settings()
    user_agent = f"{settings.app_name}/seed (EVE buyback)"
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(120.0), headers={"User-Agent": user_agent}
    ) as http:
        source = SdeSource(http)
        async with SessionLocal() as session:
            metadata = await seed_reference_data(
                session, source, source_label=FUZZWORK_DUMP_BASE
            )
    print(
        f"Seeded SDE from {metadata.source}: "
        f"{metadata.type_count} types, "
        f"{metadata.market_group_count} market groups "
        f"(imported_at={metadata.imported_at.isoformat()})"
    )


if __name__ == "__main__":
    asyncio.run(main())
