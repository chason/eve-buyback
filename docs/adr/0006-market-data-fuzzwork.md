# 0006. Market data from Fuzzwork aggregates, cached with TTL

- **Status:** Accepted
- **Date:** 2026-06-04

## Context

Pricing needs aggregated **buy/sell** numbers per item at a market hub (e.g. Jita).
ESI exposes only *raw* market orders; for a hub like The Forge that is millions of
rows to fetch and aggregate ourselves, with rate limits and storage cost. The
product's "buy/sell/split" maps directly onto pre-computed aggregates.

## Decision

Source prices from the **Fuzzwork market aggregates API**:
`GET https://market.fuzzwork.co.uk/aggregates/?station=<hub>&types=<csv>`, which
returns per-`type_id` `buy`/`sell` objects (`weightedAverage`, `max`, `min`,
`median`, `percentile`, `volume`, `orderCount`). Requests are **batched** by type
id and **cached** in a `MarketPrice` table with `fetched_at` and a TTL (~1h).
Quotes read cache-first and fetch only misses/stale entries.

## Consequences

- Drastically less compute/storage than self-aggregating ESI orders; faster quotes.
- Introduces a third-party dependency: handle outages/timeouts gracefully (serve
  slightly-stale cache, mark items unpriced rather than failing the whole quote).
- The aggregate *field* used for buy/sell is configurable per corp
  (default `percentile` for manipulation resistance); `split = (buy + sell) / 2`.
- Hub is configurable (default Jita 4-4 station `60003760`, region fallback The
  Forge `10000002`).
- Send a descriptive `User-Agent` (per the `eve-esi` skill) and respect Fuzzwork's
  caching/rate expectations.

## Alternatives considered

- **Raw ESI market orders, aggregate in-house** — full control and no third party,
  but heavy and operationally costly; revisit only if Fuzzwork proves insufficient.
- **Adam4EVE / EVE Ref** — viable alternates; kept as fallbacks behind the market
  client interface so the source can be swapped without touching the pricing engine.
