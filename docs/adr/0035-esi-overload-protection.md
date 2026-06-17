# 0035. ESI-overload protection for appraisals

- **Status:** Accepted
- **Date:** 2026-06-17
- **Addresses:** security audit Top Risk #1 (#23)
- **Relates to:** [ADR-0028](0028-esi-market-source-and-aggregation.md) (the ESI market
  source + per-type fan-out), [ADR-0034](0034-background-market-refresh.md) (the background
  refresh that warms caches), [ADR-0010](0010-in-process-scheduling.md) (single in-process,
  no broker)

## Context

Pricing a **non-Fuzzwork** hub queries ESI: a region (NPC station) hub does **one
paginated `markets/{region}/orders/` request per type** (`esi_market.get_region_aggregates`).
An authenticated member can submit a 1000-item appraisal of distinct types at such a hub,
firing ~1000 multi-page outbound requests; worse, each call previously got its **own**
`Semaphore(esi_market_concurrency=8)`, so N concurrent appraisals ran 8·N in flight.
Repeated, this exhausts ESI's per-IP error budget (degrading **all** pricing) and ties up
the server. No rate limiting or per-request bound existed.

The background refresh (ADR-0034) keeps the region **hot set** warm and pre-warms whole
**structure** books, so legitimate/repeat appraisals serve from cache. But it deliberately
does **not** pre-warm cold/never-appraised **region** types ("all types" = one ESI request
per type, intractable). So the cache doesn't cover the attack — an attacker picks rare
type_ids that are guaranteed cache misses. The defense must bound the worst case directly.

## Decision

Two layered bounds (a per-user rate limiter and a full circuit breaker are **deferred** —
see below):

1. **Per-appraisal cap on distinct ESI-priced types.** In `create_appraisal`, after the
   types are grouped by hub (`ids_by_hub`), count those whose `resolve_market_source(hub)`
   is **not** `fuzzwork` and reject with `AppraisalTooManyEsiTypes` (HTTP **422**) **before
   any pricing** when it exceeds `max_esi_types_per_appraisal` (default **100**). This caps
   one request's outbound fan-out regardless of cache state. Fuzzwork hubs batch into a
   single request and are exempt (bounded only by the existing 1000-item cap).
2. **Process-wide ESI concurrency cap.** One `asyncio.Semaphore(esi_market_concurrency)` is
   created in the app lifespan (`app.state.esi_semaphore`) and injected into every
   `EsiMarketClient` (requests **and** the background refresh). `get_region_aggregates`
   acquires the **shared** semaphore instead of constructing a per-call one. This repurposes
   `esi_market_concurrency` from a per-call to a **global** bound, so concurrent appraisals
   can no longer multiply outbound load. (The client falls back to a per-call semaphore when
   none is injected, keeping unit tests simple.)

Together: outbound ESI is hard-bounded — **per request ≤ the type cap, globally ≤ the
semaphore**.

## Consequences

- The audited ESI-budget exhaustion is closed with a small, dependency-free change; the 422
  surfaces on the Appraise page via the existing SPA error display (PR #18).
- `esi_market_concurrency`'s meaning changes (per-call → process-wide); documented in the
  setting docstring and `.env.example`.
- **Multi-instance caveat:** the semaphore is per-process, so M app instances allow up to
  M× the global cap of concurrent ESI calls. Acceptable for the single-process self-host
  today (ADR-0010); a shared limiter is the same scale-out follow-up the scheduler/cache
  carry.
- The existing ESI error-limit backoff (`esi_market._respect_error_limit`) remains as the
  reactive backstop.

## Alternatives considered / deferred

- **Per-user rate limiting** on `POST /appraisals` + structure search — with the warm cache
  (ADR-0034) and the two bounds above already capping outbound ESI, rate limiting drops to
  general request-flood / server-resource hardening rather than ESI-budget protection.
  **Deferred to its own issue (#96).**
- **A full ESI circuit breaker** (trip global pricing to cache-only on an error spike,
  auto-recover) — meaningfully more state/tests; the error-limit backoff covers the reactive
  case for now. Deferred.
- **Per-IP limiting** — needs trusted-proxy `X-Forwarded-For` handling behind Traefik/Coolify;
  the audited attack is an authenticated member, so per-user is the precise key when we do
  add a limiter.
