from app.application.sde import seed_reference_data
from app.data.db import SessionLocal
from app.data.repositories import sde as sde_repo
from app.plugins.sde_source import SdeMarketGroupRow, SdeTypeRow

GROUPS = [
    SdeMarketGroupRow(market_group_id=1, parent_id=None, name="Ore"),
    SdeMarketGroupRow(market_group_id=2, parent_id=1, name="Moon Ores"),
]

TYPES = [
    SdeTypeRow(type_id=34, name="Tritanium", group_id=18, market_group_id=1,
               volume="0.01", published=True),
    SdeTypeRow(type_id=46680, name="Veldspar Stuff", group_id=465,
               market_group_id=2, volume="0.1", published=True),
    # Dropped: unpublished even though it has a market group.
    SdeTypeRow(type_id=11111, name="Removed Item", group_id=99,
               market_group_id=1, volume="1.0", published=False),
    # Dropped: published but not market-tradeable (no market group).
    SdeTypeRow(type_id=22222, name="Blueprint Original", group_id=10,
               market_group_id=None, volume="0.0", published=True),
]


class FakeSdeSource:
    def __init__(self, types, groups):
        self._types = types
        self._groups = groups

    async def fetch_types(self):
        return self._types

    async def fetch_market_groups(self):
        return self._groups


async def test_seed_keeps_only_published_market_types():
    source = FakeSdeSource(TYPES, GROUPS)
    async with SessionLocal() as session:
        meta = await seed_reference_data(session, source, source_label="test")

    assert meta.type_count == 2
    assert meta.market_group_count == 2
    assert meta.source == "test"

    async with SessionLocal() as session:
        assert (await sde_repo.get_type(session, 34)) is not None
        assert (await sde_repo.get_type(session, 46680)) is not None
        assert (await sde_repo.get_type(session, 11111)) is None  # unpublished
        assert (await sde_repo.get_type(session, 22222)) is None  # no market group
        groups = await sde_repo.list_market_groups(session)
        assert {g.market_group_id for g in groups} == {1, 2}


async def test_seed_is_idempotent():
    source = FakeSdeSource(TYPES, GROUPS)
    async with SessionLocal() as session:
        await seed_reference_data(session, source, source_label="test")
    async with SessionLocal() as session:
        meta = await seed_reference_data(session, source, source_label="test")

    assert meta.type_count == 2
    async with SessionLocal() as session:
        rows = await sde_repo.search_types(session, "", 100)
    assert len(rows) == 2  # no duplicates after a second run
