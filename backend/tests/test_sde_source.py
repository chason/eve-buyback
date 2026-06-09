"""SdeSource reads Fuzzwork's plain-CSV dumps (with a UTF-8 BOM) — ADR-0009.

Guards the transport contract that bit us in production: Fuzzwork moved the dumps
to /dump/latest/csv/ as uncompressed, BOM-prefixed CSV. A revert to bzip2 decoding
or dropping utf-8-sig would break the first-column header and fail these.
"""

import httpx

from app.plugins import sde_source
from app.plugins.sde_source import SdeSource

# Leading ﻿ = UTF-8 BOM, exactly as Fuzzwork serves it.
MARKET_GROUPS_CSV = (
    '﻿"marketGroupID","parentGroupID","marketGroupName","description"\r\n'
    '"2","","Blueprints & Reactions","desc"\r\n'
    '"5","2","Standard Ores","desc"\r\n'
)
TYPES_CSV = (
    '﻿"typeID","groupID","typeName","volume","portionSize","published","marketGroupID"\r\n'
    '"34","18","Tritanium","0.01","1","1","18"\r\n'
)


def _client_returning(body: str) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body.encode("utf-8"))

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_fetch_market_groups_strips_bom_and_parses():
    async with _client_returning(MARKET_GROUPS_CSV) as client:
        groups = await SdeSource(client).fetch_market_groups()
    assert [(g.market_group_id, g.parent_id, g.name) for g in groups] == [
        (2, None, "Blueprints & Reactions"),
        (5, 2, "Standard Ores"),
    ]


async def test_fetch_types_reads_bom_first_column():
    async with _client_returning(TYPES_CSV) as client:
        types = await SdeSource(client).fetch_types()
    assert len(types) == 1
    t = types[0]
    # type_id comes from the BOM-prefixed first column — must resolve cleanly.
    assert (t.type_id, t.name, t.portion_size, t.published) == (34, "Tritanium", 1, True)


def test_dump_urls_are_plain_csv_under_csv_subdir():
    # Lock the current Fuzzwork layout: csv/ subdir, no .bz2.
    assert sde_source.FUZZWORK_DUMP_BASE.endswith("/dump/latest/csv")
    for url in (
        sde_source.INV_TYPES_URL,
        sde_source.INV_MARKET_GROUPS_URL,
        sde_source.INV_GROUPS_URL,
        sde_source.INV_TYPE_MATERIALS_URL,
    ):
        assert url.endswith(".csv") and not url.endswith(".bz2")
