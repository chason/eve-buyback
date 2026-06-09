from app.application.sde import seed_if_needed, seed_reference_data
from app.data.db import SessionLocal
from app.data.repositories import sde as sde_repo
from app.plugins.sde_source import (
    SdeMarketGroupRow,
    SdeStationRow,
    SdeTypeMaterialRow,
    SdeTypeRow,
)

GROUPS = [
    SdeMarketGroupRow(market_group_id=1, parent_id=None, name="Ore"),
    SdeMarketGroupRow(market_group_id=2, parent_id=1, name="Moon Ores"),
]

# group 18 = Mineral (category 4); group 465 = an ore group (category 25 Asteroid).
GROUP_CATEGORIES = {18: 4, 465: 25, 99: 4, 10: 9}

TYPES = [
    SdeTypeRow(type_id=34, name="Tritanium", group_id=18, market_group_id=1,
               volume="0.01", portion_size=1, published=True),
    SdeTypeRow(type_id=46680, name="Veldspar Stuff", group_id=465,
               market_group_id=2, volume="0.1", portion_size=100, published=True),
    # Dropped: unpublished even though it has a market group.
    SdeTypeRow(type_id=11111, name="Removed Item", group_id=99,
               market_group_id=1, volume="1.0", portion_size=1, published=False),
    # Dropped: published but not market-tradeable (no market group).
    SdeTypeRow(type_id=22222, name="Blueprint Original", group_id=10,
               market_group_id=None, volume="0.0", portion_size=1, published=True),
]

MATERIALS = [
    # The ore (46680) reprocesses to Tritanium — kept.
    SdeTypeMaterialRow(type_id=46680, material_type_id=34, quantity=333),
    # A non-ore type's materials (34 is a mineral) — filtered out by the seed.
    SdeTypeMaterialRow(type_id=34, material_type_id=99, quantity=1),
]

STATIONS = [
    SdeStationRow(
        station_id=60012345,
        name="Korsiki VII - Moon 1 - Expert Distribution Warehouse",
        system_id=30000001,
        region_id=10000033,
    ),
]
SYSTEMS = {30000001: "Korsiki"}


class FakeSdeSource:
    def __init__(self, types, groups):
        self._types = types
        self._groups = groups

    async def fetch_types(self):
        return self._types

    async def fetch_market_groups(self):
        return self._groups

    async def fetch_group_categories(self):
        return GROUP_CATEGORIES

    async def fetch_type_materials(self):
        return MATERIALS

    async def fetch_stations(self):
        return STATIONS

    async def fetch_systems(self):
        return SYSTEMS


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


async def test_seed_tags_category_portion_and_ore_materials():
    source = FakeSdeSource(TYPES, GROUPS)
    async with SessionLocal() as session:
        await seed_reference_data(session, source, source_label="test")

    async with SessionLocal() as session:
        ore = await sde_repo.get_type(session, 46680)
        assert ore.category_id == 25 and ore.portion_size == 100
        # Only the ore's materials are seeded; the mineral's row is filtered out.
        mats = await sde_repo.get_type_materials(session, [46680, 34])
        assert mats == {46680: [(34, 333)]}


async def test_seed_stations_join_system_name():
    source = FakeSdeSource(TYPES, GROUPS)
    async with SessionLocal() as session:
        await seed_reference_data(session, source, source_label="test")

    async with SessionLocal() as session:
        station = await sde_repo.get_station(session, 60012345)
        assert station is not None
        assert station.system_name == "Korsiki"  # joined from mapSolarSystems
        assert station.region_id == 10000033
        # Searchable by system name or station name.
        by_system = await sde_repo.search_stations(session, "korsiki", 10)
        by_station = await sde_repo.search_stations(session, "expert distr", 10)
        assert [s.station_id for s in by_system] == [60012345]
        assert [s.station_id for s in by_station] == [60012345]


async def test_seed_if_needed_seeds_then_skips():
    source = FakeSdeSource(TYPES, GROUPS)
    # Empty DB → seeds.
    async with SessionLocal() as session:
        first = await seed_if_needed(session, source, source_label="test")
    assert first is not None
    assert first.type_count == 2
    # Already complete → cheap no-op (returns None, no re-download).
    async with SessionLocal() as session:
        second = await seed_if_needed(session, source, source_label="test")
    assert second is None


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
