"""Pure rules for the hangar read (ADR-0044). No I/O: the use case fetches the corp's
assets and its configured buyback hangars and feeds them here as plain data; this
decides what physically counts as "in the buyback hangar"."""

from dataclasses import dataclass


@dataclass(frozen=True)
class HangarKey:
    """One configured buyback hangar: a station/structure + a corp hangar division.
    `location_id` is a string so it holds 64-bit structure ids (ADR-0029)."""

    location_id: str
    division: int  # 1..7 → location_flag CorpSAG1..CorpSAG7


@dataclass(frozen=True)
class AssetStack:
    """One asset row reduced to what the count needs (plugin-agnostic). `item_id`
    matters because containment is expressed through it: an item inside a container
    (or a ship) has the parent's `item_id` as its own `location_id`."""

    item_id: int
    type_id: int
    quantity: int
    location_id: int
    location_flag: str


def division_flag(division: int) -> str:
    """The ESI `location_flag` for a corp hangar division (CorpSAG1…CorpSAG7)."""
    return f"CorpSAG{division}"


def hangar_counts(
    assets: list[AssetStack], hangars: list[HangarKey]
) -> dict[tuple[str, int], int]:
    """The physical count per `(location_id, type_id)` across the configured buyback
    hangars — the stock-take side of the reconciliation (ADR-0044). Counts everything
    physically inside a configured hangar division, **including the contents of
    containers** (and anything nested deeper): buybacks routinely organize hangars
    with station containers, and their contents are still buyback stock. Contained
    items attribute to the hangar's station, since that's the granularity the ledger
    tracks. The containers themselves count too — they're physically there, and the
    reconciliation must not read them as missing."""
    wanted = {(h.location_id, division_flag(h.division)) for h in hangars}
    counts: dict[tuple[str, int], int] = {}
    # item_id → the marked hangar's station, for everything known to be inside one.
    station_of: dict[int, str] = {}

    def _count(asset: AssetStack, station: str) -> None:
        station_of[asset.item_id] = station
        slot = (station, asset.type_id)
        counts[slot] = counts.get(slot, 0) + asset.quantity

    remaining: list[AssetStack] = []
    for asset in assets:
        if (str(asset.location_id), asset.location_flag) in wanted:
            _count(asset, str(asset.location_id))
        else:
            remaining.append(asset)

    # Fixpoint over containment: an asset whose location_id is an item already inside
    # a marked hangar is inside it too (container in hangar → items in container →
    # items nested deeper). Each pass resolves one nesting level.
    while True:
        unresolved: list[AssetStack] = []
        progressed = False
        for asset in remaining:
            station = station_of.get(asset.location_id)
            if station is not None:
                _count(asset, station)
                progressed = True
            else:
                unresolved.append(asset)
        if not progressed:
            return counts
        remaining = unresolved
