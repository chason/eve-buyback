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
    """One asset row reduced to what the count needs (plugin-agnostic)."""

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
    hangars — the stock-take side of the reconciliation (ADR-0044). Only items sitting
    DIRECTLY in a configured hangar division count: an item inside a container has the
    container's item_id as its location_id and is deliberately excluded (its contents
    aren't hangar-visible stock)."""
    wanted = {(h.location_id, division_flag(h.division)) for h in hangars}
    counts: dict[tuple[str, int], int] = {}
    for asset in assets:
        key = (str(asset.location_id), asset.location_flag)
        if key not in wanted:
            continue
        slot = (str(asset.location_id), asset.type_id)
        counts[slot] = counts.get(slot, 0) + asset.quantity
    return counts
