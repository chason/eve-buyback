"""Reader for Fuzzwork's SDE CSV conversions, used by the deploy-time seed (ADR-0009).

A plugin (outside-API gateway): downloads the bzip2-compressed CSV dumps, parses
them, and yields faithful Pydantic rows. It does **no** filtering — the seed use
case decides which rows to keep — so the source stays a dumb, testable reader.
"""

import bz2
import csv
import io
from decimal import Decimal

import httpx
from pydantic import BaseModel

FUZZWORK_DUMP_BASE = "https://www.fuzzwork.co.uk/dump/latest"
INV_TYPES_URL = f"{FUZZWORK_DUMP_BASE}/invTypes.csv.bz2"
INV_MARKET_GROUPS_URL = f"{FUZZWORK_DUMP_BASE}/invMarketGroups.csv.bz2"
INV_GROUPS_URL = f"{FUZZWORK_DUMP_BASE}/invGroups.csv.bz2"
INV_TYPE_MATERIALS_URL = f"{FUZZWORK_DUMP_BASE}/invTypeMaterials.csv.bz2"


class SdeTypeRow(BaseModel):
    type_id: int
    name: str
    group_id: int
    market_group_id: int | None
    volume: Decimal
    portion_size: int
    published: bool


class SdeMarketGroupRow(BaseModel):
    market_group_id: int
    parent_id: int | None
    name: str


class SdeTypeMaterialRow(BaseModel):
    type_id: int
    material_type_id: int
    quantity: int


def _int_or_none(value: str | None) -> int | None:
    value = (value or "").strip()
    return int(value) if value and value.lower() != "none" else None


class SdeSource:
    """Fetches and parses Fuzzwork's invTypes / invMarketGroups CSV dumps."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def _fetch_csv(self, url: str) -> csv.DictReader:
        resp = await self._client.get(url)
        resp.raise_for_status()
        text = bz2.decompress(resp.content).decode("utf-8")
        return csv.DictReader(io.StringIO(text))

    async def fetch_types(self) -> list[SdeTypeRow]:
        reader = await self._fetch_csv(INV_TYPES_URL)
        return [
            SdeTypeRow(
                type_id=int(row["typeID"]),
                name=row["typeName"],
                group_id=int(row["groupID"]),
                market_group_id=_int_or_none(row.get("marketGroupID")),
                volume=Decimal(row["volume"]) if row.get("volume") else Decimal("0"),
                portion_size=int(row["portionSize"]) if row.get("portionSize") else 1,
                published=row.get("published", "0").strip() == "1",
            )
            for row in reader
        ]

    async def fetch_market_groups(self) -> list[SdeMarketGroupRow]:
        reader = await self._fetch_csv(INV_MARKET_GROUPS_URL)
        return [
            SdeMarketGroupRow(
                market_group_id=int(row["marketGroupID"]),
                parent_id=_int_or_none(row.get("parentGroupID")),
                name=row["marketGroupName"],
            )
            for row in reader
        ]

    async def fetch_group_categories(self) -> dict[int, int]:
        """Map `group_id -> category_id` (from invGroups), for tagging each type's
        category (ores are category 25)."""
        reader = await self._fetch_csv(INV_GROUPS_URL)
        return {
            int(row["groupID"]): int(row["categoryID"])
            for row in reader
            if row.get("categoryID")
        }

    async def fetch_type_materials(self) -> list[SdeTypeMaterialRow]:
        """Reprocessing yields from invTypeMaterials (base/100% quantities)."""
        reader = await self._fetch_csv(INV_TYPE_MATERIALS_URL)
        return [
            SdeTypeMaterialRow(
                type_id=int(row["typeID"]),
                material_type_id=int(row["materialTypeID"]),
                quantity=int(row["quantity"]),
            )
            for row in reader
        ]
