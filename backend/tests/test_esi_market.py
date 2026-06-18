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


async def test_structure_name_resolution_respects_injected_semaphore():
    # The typeahead name fan-out (#26) shares the same size-bounded cap, so a rapid
    # search can't multiply outbound ESI structure-name lookups.
    client = EsiMarketClient(client=None, semaphore=asyncio.Semaphore(1))

    active = 0
    peak = 0

    async def fake_name(*, structure_id, access_token):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)  # hold the slot so a missing cap would overlap
        active -= 1
        return f"Structure {structure_id}"

    client.resolve_structure_name = fake_name

    names = await client.resolve_structure_names(
        structure_ids=[1, 2, 3, 4, 5], access_token="t"
    )

    assert peak == 1  # never more than one in flight under the size-1 cap
    assert names == {sid: f"Structure {sid}" for sid in [1, 2, 3, 4, 5]}


async def test_structure_name_resolution_drops_inaccessible():
    client = EsiMarketClient(client=None, semaphore=asyncio.Semaphore(5))

    async def fake_name(*, structure_id, access_token):
        return None if structure_id == 2 else f"Structure {structure_id}"

    client.resolve_structure_name = fake_name

    names = await client.resolve_structure_names(
        structure_ids=[1, 2, 3], access_token="t"
    )
    assert names == {1: "Structure 1", 3: "Structure 3"}  # 2 (None) dropped
