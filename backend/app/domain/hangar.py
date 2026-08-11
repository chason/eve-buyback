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
    reconciliation must not read them as missing.

    Corp hangar rows do NOT sit at the station directly: ESI parents them under the
    corp's OFFICE there — the division-flagged row's `location_id` is the office's
    `item_id`, and only the office row (flag "OfficeFolder") points at the station.
    So any row sitting AT a marked location anchors its children: a child whose own
    flag is the marked division counts, wherever the division flag actually hangs
    (under the office in real data; directly at the location in the degenerate
    shape). The office row itself is not stock — it never carries a CorpSAG flag —
    and children under an anchor count ONLY on their own division flag, so nothing
    at the station leaks in by mere adjacency."""
    wanted = {(h.location_id, division_flag(h.division)) for h in hangars}
    marked_locations = {h.location_id for h in hangars}
    # item_id → marked location, for rows sitting AT a marked location (the
    # office, in real data) — the anchor the division-flagged children hang from.
    anchors: dict[int, str] = {}
    for asset in assets:
        if str(asset.location_id) in marked_locations:
            anchors[asset.item_id] = str(asset.location_id)

    counts: dict[tuple[str, int], int] = {}
    # item_id → the marked hangar's station, for everything known to be inside one.
    station_of: dict[int, str] = {}

    def _count(asset: AssetStack, station: str) -> None:
        station_of[asset.item_id] = station
        slot = (station, asset.type_id)
        counts[slot] = counts.get(slot, 0) + asset.quantity

    remaining: list[AssetStack] = []
    for asset in assets:
        station = anchors.get(asset.location_id, str(asset.location_id))
        if (station, asset.location_flag) in wanted:
            _count(asset, station)
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
