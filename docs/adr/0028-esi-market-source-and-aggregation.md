# 0028. ESI as the market source for hubs Fuzzwork doesn't cover

- **Status:** Accepted
- **Date:** 2026-06-09
- **Extends:** [ADR-0006](0006-market-data-fuzzwork.md)

## Context

Fuzzwork ([ADR-0006](0006-market-data-fuzzwork.md)) only aggregates the **five NPC
trade hubs** (Jita/Amarr/Dodixie/Rens/Hek) plus whole regions. A corp that operates
out of any other NPC station can't price there. We want to let a manager point the
hub at an arbitrary station and have the app price it from **EVE ESI** instead —
without changing how the 5 covered hubs work, and without disturbing the price cache
or the pricing engine.

ESI exposes only **raw orders** (`GET markets/{region_id}/orders/?type_id=…`), not
aggregates, so we must compute the same per-side figures Fuzzwork returns.

## Decision

Keep the single read-through entry point `application/market.py::get_market_prices`
and **branch it on a resolved hub descriptor** to choose the source that fills cache
misses. The `market_prices` table (PK `(hub_id, type_id)`, 7 fields per side) and the
TTL/graceful-degradation logic are unchanged — only *where* a missed row comes from.

- **Hub descriptor** — `BuybackConfig` gains `market_hub_kind`
  (`npc_station`|`structure`), `market_region_id`, and a cached `market_hub_name`.
  `domain/market.resolve_market_source(hub)` returns `fuzzwork` when the station id is
  one of the five covered hubs, else `esi_region` (other NPC station), else
  `esi_structure` (a later ADR). There is **no user-facing "Fuzzwork" kind** — the
  source is derived from the id, not chosen.
- **ESI region orders** (`plugins/esi_market.py`) — one paginated request per type
  (`X-Pages`), fanned out under a concurrency cap (`esi_market_concurrency`, default
  8), filtered to the station's `location_id`. Order prices are parsed JSON-number →
  `Decimal` directly (`parse_float=Decimal`) to avoid any float round-trip (ADR-0020).
  ESI's error budget is respected (`X-Esi-Error-Limit-Remain` backoff); one type's
  failure is logged and skipped, never fatal.
- **Aggregation** (`domain/aggregates.py`, pure, Decimal) reproduces Fuzzwork's
  semantics so cached rows are source-interchangeable: volume-weighted **percentile**
  (best 5% of volume, including the fractional contribution of the boundary order),
  volume-weighted **median** (50% mark), **weighted_average**, plain **max/min**,
  **volume** = Σ`volume_remain`, **order_count**. An empty side is an all-zero
  aggregate with `order_count = 0`, which the appraisal's `order_count > 0` gate
  already treats as "no orders" and rejects.
- **Region resolution at save time** — when a manager sets a non-Fuzzwork station,
  `update_config` resolves and caches its region id + name via ESI
  (station→system→constellation→region) once; an unresolvable id is rejected
  (`MarketHubInvalid` → 422). The hot path never touches the universe endpoints.

## Consequences

- Any NPC station is now priceable, configurable in the UI, with no token storage —
  it stays within [ADR-0004](0004-eve-sso-session-auth.md).
- ESI is one request **per type** (Fuzzwork batches 200), so a cold cache for a busy
  appraisal is many requests; the existing `(hub_id, type_id)` TTL cache means only
  misses are fetched, and the concurrency cap + per-type isolation bound the load.
- **Aggregation parity is the main risk.** Fuzzwork's exact percentile/median aren't
  formally published; the volume-weighted 5%/50% definition here is the
  community-standard reading and is **authoritative for ESI hubs**. The 5 Fuzzwork
  hubs never go through ESI, so a single `(hub_id, type_id)` row is never a mix of
  sources.
- Outage handling is uniform: an ESI failure degrades to stale cache / omits unpriced,
  exactly as a Fuzzwork outage does.
- Structures (`esi_structure`) need an authenticated, persisted refresh token; that
  supersedes [ADR-0004](0004-eve-sso-session-auth.md) and is deferred to its own ADR.

## Alternatives considered

- **Fetch the whole region's order book once and index by type** — avoids per-type
  requests but pulls hundreds of thousands of rows for a busy region; exactly the cost
  ADR-0006 set out to avoid. Per-type with the `type_id` filter keeps responses small.
- **A separate ESI price table / a `source` column** — the existing source-agnostic
  cache already stores whatever the aggregator produces; a parallel table or
  provenance column adds schema with no behavioural benefit for the MVP.
- **A user-facing "Fuzzwork vs ESI" toggle** — leaks an implementation detail; the
  source is fully determined by the station id, so it's derived, not chosen.
