"""Pure market-data helpers (no I/O)."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

# Where a corp prices: a public NPC station or a player Upwell structure (ADR-0028).
HubKind = Literal["npc_station", "structure"]

# The five NPC trade hubs Fuzzwork aggregates (ADR-0006). Any other NPC station is
# priced from ESI region orders instead; structures always come from ESI.
FUZZWORK_HUBS: frozenset[int] = frozenset(
    {
        60003760,  # Jita IV - Moon 4 - Caldari Navy Assembly Plant
        60008494,  # Amarr VIII (Oris) - Emperor Family Academy
        60011866,  # Dodixie IX - Moon 20 - Federation Navy Assembly Plant
        60004588,  # Rens VI - Moon 8 - Brutor Tribe Treasury
        60005686,  # Hek VIII - Moon 12 - Boundless Creation Factory
    }
)

# Display names for the Fuzzwork hubs (used when one is selected, so we don't need an
# ESI round-trip to label it).
FUZZWORK_HUB_NAMES: dict[int, str] = {
    60003760: "Jita IV - Moon 4 - Caldari Navy Assembly Plant",
    60008494: "Amarr VIII (Oris) - Emperor Family Academy",
    60011866: "Dodixie IX - Moon 20 - Federation Navy Assembly Plant",
    60004588: "Rens VI - Moon 8 - Brutor Tribe Treasury",
    60005686: "Hek VIII - Moon 12 - Boundless Creation Factory",
}

# Which source fills cache misses for a hub.
MarketSource = Literal["fuzzwork", "esi_region", "esi_structure"]


@dataclass(frozen=True)
class HubDescriptor:
    """A corp's configured market hub, resolved enough to choose a price source.
    `region_id` is required for `esi_region` (NPC station) pricing; unused for the
    Fuzzwork hubs and for structures (whose endpoint is structure-scoped)."""

    hub_id: int  # location_id: NPC station id or structure id
    kind: HubKind
    region_id: int | None = None


def resolve_market_source(hub: HubDescriptor) -> MarketSource:
    """Pick the price source for a hub: Fuzzwork for the five covered NPC hubs, ESI
    region orders for any other NPC station, ESI structure markets for structures."""
    if hub.kind == "structure":
        return "esi_structure"
    return "fuzzwork" if hub.hub_id in FUZZWORK_HUBS else "esi_region"


def is_fresh(fetched_at: datetime, *, now: datetime, ttl_seconds: int) -> bool:
    """True if a cached price fetched at `fetched_at` is still within its TTL."""
    return now - fetched_at < timedelta(seconds=ttl_seconds)
