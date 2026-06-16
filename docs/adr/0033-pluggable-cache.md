# 0033. Pluggable cache (L1) for market prices

- **Status:** Accepted
- **Date:** 2026-06-12
- **Relates to:** [ADR-0006](0006-market-data-fuzzwork.md) / [ADR-0028](0028-esi-market-source-and-aggregation.md)
  (the market-price source + `market_prices` DB cache), [ADR-0018](0018-layered-backend-architecture.md)
  (plugins are outside-resource gateways)

## Context

Pricing a non-Fuzzwork hub queries **ESI** — one paginated request per type for region
orders, or a full structure order-book sweep. The `market_prices` DB table already
read-through-caches the computed aggregates per `(hub_id, type_id)` with a 1h TTL
(`application/market.py`), so ESI isn't re-queried within the hour. We want a **faster,
process-shareable cache tier in front of that DB cache**: in-memory for the current
single-tenant / single-process deploy, swappable to **memcached** by configuration
alone (no call-site changes) once it goes multi-process / multi-tenant and a *shared*
cache can offload both the DB and ESI.

## Decision

Introduce a small **pluggable cache port** (`plugins/cache.py`) and use it as an **L1
tier in front of the durable `market_prices` DB cache** (L2).

- **The port is memcached's lowest common denominator:** `get(key) -> bytes | None`,
  `set(key, value, *, ttl_seconds)`, `delete(key)`, `aclose()` — string keys, opaque
  bytes values, per-key TTL, and **no** enumerate/clear. Shaping it this way means the
  in-memory default can't acquire a habit the memcached adapter couldn't keep, so
  swapping backends never touches a call site. `safe_key(*parts)` sha1-hashes any key
  that would exceed memcached's 250-byte / no-whitespace limits; `get_model`/`set_model`
  serialize Pydantic models as JSON so the port stays bytes-pure.
- **Two adapters, one factory.** `MemoryCache` (bounded LRU + per-entry TTL on a
  monotonic clock; the default) and `MemcachedCache` (aiomcache, lazy-imported so it's
  only a dependency when selected). `build_cache(settings)` switches on
  `BUYBACK_CACHE_BACKEND`; the cache is built once in the app lifespan and injected
  per request like the shared httpx client.
- **L1 over L2, three tiers.** `get_market_prices(..., cache=None)` reads L1 first
  (hits are fresh by construction — the cache TTL enforces it), then the DB cache for
  L1 misses (promoting fresh DB rows into L1), then the source (writing fresh fetches
  to both DB and L1). `cache=None` skips L1 entirely — identical to the prior two-tier
  behavior, which keeps direct callers and tests unchanged.
- **Only fresh data enters L1.** On a source outage the request still degrades to
  *stale* DB rows (ADR-0028), but those are **not** written to L1, so the next request
  retries the source instead of locking in a stale value.
- The **DB stays the durable tier**: it survives restarts/deploys, so a redeploy
  doesn't cold-hit ESI (whose error budget we must respect). memcached, like the memory
  cache, is empty on restart — hence L1, not replacement.

## Consequences

- Swapping memory → memcached is `BUYBACK_CACHE_BACKEND=memcached` +
  `BUYBACK_MEMCACHED_ADDR`, no code change. `aiomcache` ships as a dependency but is
  only imported when that backend is selected; CI exercises the memory path and the
  memcached adapter against a fake client (no memcached service required).
- The L1 caches all sources uniformly (Fuzzwork benefits too); its TTL
  (`market_l1_cache_ttl_seconds`, default 60s) is independent of and ≤ the DB TTL.
- The port is reusable for other lookups later (SDE, structure names) but only market
  prices use it now.

## Alternatives considered

- **Replace the DB cache** with the pluggable cache — one mechanism, but a restart
  empties it and the next appraisals re-hit ESI/Fuzzwork cold; the DB's durability is
  exactly what protects ESI's error budget across deploys. Rejected.
- **Cache raw ESI order books** below the DB aggregate cache (so two stations in one
  region share a fetch) — ESI-specific and useful, but a narrower optimization than a
  general price L1; a possible later addition, not this ADR.
- **A richer port** (counters, get-or-set, pattern delete) — every addition narrows the
  set of swappable backends; kept to the four primitives memcached guarantees.

## Out of scope

- Request coalescing / stampede control — concurrent L1+L2 misses still each fetch.
