"""ESI market client: the process-wide concurrency cap (ADR-0035)."""

import asyncio

from app.plugins.esi_market import EsiMarketClient


async def test_region_aggregates_respects_injected_semaphore():
    # A shared size-1 semaphore serializes the per-type region fan-out, so concurrent
    # appraisals can't multiply outbound ESI requests (ADR-0035).
    client = EsiMarketClient(client=None, semaphore=asyncio.Semaphore(1))

    active = 0
    peak = 0

    async def fake_orders(region_id, type_id):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)  # hold the slot so a missing cap would overlap
        active -= 1
        return []

    client._region_orders_for_type = fake_orders

    await client.get_region_aggregates(
        region_id=1, station_id="x", type_ids=[1, 2, 3, 4, 5]
    )

    assert peak == 1  # never more than one in flight under the size-1 cap


async def test_region_aggregates_allows_concurrency_up_to_the_cap():
    client = EsiMarketClient(client=None, semaphore=asyncio.Semaphore(3))

    active = 0
    peak = 0

    async def fake_orders(region_id, type_id):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return []

    client._region_orders_for_type = fake_orders

    await client.get_region_aggregates(
        region_id=1, station_id="x", type_ids=list(range(10))
    )

    assert peak == 3  # fans out, but bounded by the cap
