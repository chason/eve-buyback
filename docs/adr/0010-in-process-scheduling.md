# 0010. In-process scheduling, no external broker

- **Status:** Accepted
- **Date:** 2026-06-04
- **Implemented by:** [ADR-0034](0034-background-market-refresh.md) — the APScheduler
  job reserved here now refreshes non-Fuzzwork hub prices in the background.

## Context

Some work runs outside a request: refreshing cached market prices
([ADR-0006](0006-market-data-fuzzwork.md)) and possibly periodic housekeeping. A
classic answer (Celery + Redis/RabbitMQ) would violate the "no other software"
self-hosting goal.

## Decision

Run background work **in-process**. Market data is fetched **lazily on demand**
(cache-miss/stale during a quote); any periodic refresh of hot types uses
**APScheduler** started from the FastAPI lifespan and cancelled on shutdown. No
external broker or worker process in the MVP.

## Consequences

- Single process to deploy; nothing extra to install — aligns with
  [ADR-0012](0012-single-deployable-packaging.md).
- Background jobs share the app's event loop and DB session factory; keep them
  short and non-blocking (use httpx async).
- Caveat: with multiple app instances (future horizontal scale), in-process
  schedulers would duplicate work — at that point move to a single scheduler
  instance or a real job runner. Documented as a scale-out follow-up.
- Lazy-first means the very first quote for a cold type pays the Fuzzwork fetch
  latency; acceptable and cacheable.

## Alternatives considered

- **Celery / RQ + Redis** — robust and scalable, but adds infrastructure the MVP is
  explicitly trying to avoid.
- **OS cron / Windows Task Scheduler calling a CLI** — works for self-host but is
  platform-specific and awkward to ship; in-process is portable.
