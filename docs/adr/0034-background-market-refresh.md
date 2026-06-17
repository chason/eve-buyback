# 0034. Background auto-refresh of non-Fuzzwork market prices

- **Status:** Accepted
- **Date:** 2026-06-17
- **Implements:** [ADR-0010](0010-in-process-scheduling.md) (the in-process scheduler it
  reserved for "periodic refresh of hot types")
- **Relates to:** [ADR-0028](0028-esi-market-source-and-aggregation.md) (the ESI market
  source), [ADR-0006](0006-market-data-fuzzwork.md) / [ADR-0033](0033-pluggable-cache.md)
  (the `market_prices` DB cache + L1 tier), [ADR-0029](0029-encrypted-refresh-token-structures.md)
  (structure tokens), [ADR-0031](0031-per-rule-market-hub.md) (per-rule hub overrides)

## Context

Pricing a non-Fuzzwork hub hits **ESI** — one paginated request *per type* for an NPC
region, or a full authenticated order-book sweep for a player structure. Until now that
fetch was **lazy**: the first appraisal whose cached price had expired (or was never
fetched) paid the full ESI latency while the member waited. The `market_prices` table
read-through-caches the aggregates per `(hub_id, type_id)` with a 1h TTL and ADR-0033
added an L1 tier, but nothing **proactively** kept those entries warm — so they expire
and the next member at that hub eats the slow path again.

ADR-0010 already reserved an in-process **APScheduler** job for exactly this; this ADR
builds it.

## Decision

Run a single **APScheduler `AsyncIOScheduler`** interval job from the FastAPI lifespan
([`main.py`](../../backend/app/main.py)) that proactively refreshes prices for every
hub whose source is **not Fuzzwork**. The wiring lives in
[`interface/jobs.py`](../../backend/app/interface/jobs.py); the work is a use case,
[`application/market_refresh.py`](../../backend/app/application/market_refresh.py).

- **Which hubs.** Every hub referenced by a corp's config or a pricing-rule override
  (across all tenants), filtered to `resolve_market_source(hub) != "fuzzwork"`. A
  pure-Jita deploy has none and the job is a no-op.
- **Two refresh shapes:**
  - **ESI-region NPC hubs** — refresh the already-appraised **hot set** (the hub's
    `market_prices` rows) that's about to lapse. Region pricing is one request per type,
    so "all types" is intractable; the hot set is what people actually price.
  - **Player structures** — the whole order book is a single fetch, so cache **every**
    type in it (full pre-warm), making even never-before-appraised items there instant.
- **"Due" without a new table.** A region row is due when its `fetched_at` predates
  `refresh_cutoff(now) = now − max(0, ttl − interval)` (renew anything that would expire
  before the next cycle). A structure is due when its freshest cached row predates the
  cutoff (`max(fetched_at)` proxy), or it has none yet (pre-warm). No schema change.
- **Token selection (shared cache).** `market_prices` is keyed by `hub_id` **only** —
  a hub's price is the same for everyone, so corps sharing a hub share the rows; "which
  token" decides only *who fetches*, never *what is cached*. Region orders are public
  (no token). For a structure referenced by multiple corps, the job tries their tokens
  **healthiest-first** — skip any flagged `last_refresh_failed_at`, then **least-recently
  used first** (`structure_market_tokens.last_used_at`, never-used first) — and uses the
  **first that successfully fetches**, stamping that token's `last_used_at` so the next
  cycle **rotates** to a different corp (#88). This spreads the load and exercises every
  grant (surfacing a silently-broken one sooner) instead of always leaning on one corp;
  it also handles a character losing docking access while another corp's still has it.
  The ordering is done in SQL. None usable → skip.
- **Best-effort, per hub.** A hub that errors (down ESI, revoked/denied structure token)
  is logged and skipped; the others still refresh. The job commits per hub (via the
  shared `persist_market_rows`), so partial progress survives a mid-cycle failure, and a
  top-level guard in `jobs.py` keeps the recurring job alive across anything unexpected.
  A structure that returns **403** (the character lost docking/market access) flags that
  corp's token (`last_refresh_failed_at`), which the structure-status DTO surfaces to
  managers as a "re-authorize" warning instead of a silent failure (#68); a later
  successful fetch clears the flag, so it self-heals without a re-auth. When the token is
  flagged (or missing), an appraisal that would price at that structure is **blocked**
  with an actionable `StructureMarketUnavailable` error rather than returning silently
  unpriced lines — the one place the otherwise-graceful market degrade (ADR-0028) fails
  closed, because the member can't fix a broken corp authorization themselves.
- **The DB stays the durable tier.** Refreshed prices are written through to
  `market_prices` (and promoted into L1), so a restart doesn't lose the warm set.
- **Config.** `BUYBACK_MARKET_BACKGROUND_REFRESH_ENABLED` (default on, a kill switch),
  `BUYBACK_MARKET_REFRESH_INTERVAL_SECONDS` (default 600), and
  `BUYBACK_MARKET_REFRESH_INITIAL_DELAY_SECONDS` (default 30, so a cold deploy warms soon
  without hammering ESI at boot).

## Consequences

- Appraisals at ESI-priced hubs are served warm; the lazy path remains as the fallback
  for a cold type (and for the very first appraisal before the first cycle runs).
- Only **fresh** fetches are written, exactly as the lazy path — a source outage leaves
  the existing (possibly stale) cache untouched rather than poisoning it.
- `apscheduler>=3.10,<4` is a new dependency (pure Python, no broker — consistent with
  the "no extra software" self-host goal of ADR-0010/0012).
- **Multi-instance caveat (unchanged from ADR-0010):** an in-process scheduler in every
  replica would duplicate this work under horizontal scale. Single-process today; the
  follow-up is a single scheduler instance or a leader election. Documented, not solved.
- **Security:** no new secret surface — structure refresh reuses the existing encrypted
  per-corp tokens (ADR-0029), read server-side. Region orders are public.

## Alternatives considered

- **A bare `asyncio` loop** instead of APScheduler — fewer moving parts, but ADR-0010
  named APScheduler and it gives coalescing/`max_instances` misfire handling for free.
- **A per-hub `last_refreshed` table** instead of the `max(fetched_at)` proxy — more
  precise but a schema + migration for no behavioural gain at this scale.
- **Pre-warming region hubs beyond the hot set** — ESI region pricing is per-type, so
  "every type" is thousands of requests per hub; rejected as intractable.

## Out of scope

- Per-hub backoff for a structure that fails every cycle (it simply retries and logs).
- Surfacing token/access failures to managers — tracked as a separate issue.
- A UI for refresh status / metrics.
