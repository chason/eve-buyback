"""Buyback drop-off locations (ADR-0030) — small domain types.

A location is where members deliver bought-back items (distinct from the pricing
hub). It is either an NPC station (from the SDE) or a player structure; the EVE id is
carried as a string so it can hold a 64-bit structure id (ADR-0029).
"""

from typing import Literal

LocationKind = Literal["npc_station", "structure"]


def is_valid_location_id(location_id: str) -> bool:
    """A location id must be a positive integer (a station or structure id). It is
    interpolated into authenticated ESI URLs elsewhere, so reject anything else —
    same digit guard the structure hub uses (`pricing._resolve_hub`)."""
    return location_id.isdigit()
