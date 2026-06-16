"""A small pluggable key-value cache (ADR-0033).

A **plugin** (outside-resource gateway) exposing a backend-agnostic port. The port is
shaped to memcached's lowest common denominator — string keys, opaque `bytes` values,
a per-key TTL, and **no** enumerate/clear — so the in-memory default can't grow a habit
the memcached adapter couldn't keep, and swapping backends is a config change with no
call-site edits.

Used today as an L1 tier in front of the durable `market_prices` DB cache
(`application/market.py`); the port itself knows nothing about market data.
"""

import asyncio
import hashlib
import logging
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Protocol

from fastapi import Request
from pydantic import BaseModel

from app.config import Settings

log = logging.getLogger(__name__)

# memcached limits: keys ≤ 250 bytes with no whitespace/control chars; values ≤ 1 MiB.
_MAX_KEY_LEN = 250
# memcached treats an exptime > 30 days as an absolute Unix timestamp (→ instant
# expiry) and 0 as "never expire"; clamp into the relative-seconds range so the
# adapter matches MemoryCache regardless of the caller's TTL.
_MAX_EXPTIME = 60 * 60 * 24 * 30


class Cache(Protocol):
    """Backend-agnostic cache port. Implementations: `MemoryCache`, `MemcachedCache`."""

    async def get(self, key: str) -> bytes | None: ...

    async def set(self, key: str, value: bytes, *, ttl_seconds: int) -> None: ...

    async def delete(self, key: str) -> None: ...

    async def aclose(self) -> None: ...


def safe_key(*parts: object) -> str:
    """Build a memcached-safe key from parts (joined by ':'). If the result is too
    long or contains whitespace/control chars, substitute a stable sha1 digest so any
    backend accepts it."""
    raw = ":".join(str(p) for p in parts)
    if len(raw.encode()) <= _MAX_KEY_LEN and all(
        33 <= ord(c) <= 126 for c in raw
    ):
        return raw
    return "h:" + hashlib.sha1(raw.encode()).hexdigest()


async def get_model[M: BaseModel](
    cache: Cache, key: str, model_cls: type[M]
) -> M | None:
    """Read a Pydantic model from the cache. Best-effort (ADR-0033): a backend error is
    treated as a miss, so the L1 cache can never be a hard dependency regardless of which
    `Cache` implementation is wired in. (A timeout for a *hung* backend lives in the
    adapter — a try/except here can't bound an await that never returns.)"""
    try:
        raw = await cache.get(key)
    except Exception as exc:  # noqa: BLE001 — best-effort L1: any backend error → miss
        log.warning("cache get(%s) failed; treating as miss: %r", key, exc)
        return None
    if raw is None:
        return None
    try:
        return model_cls.model_validate_json(raw)
    except ValueError:
        return None  # a schema change can leave undeserializable bytes — treat as miss


async def set_model(
    cache: Cache, key: str, value: BaseModel, *, ttl_seconds: int
) -> None:
    """Write a Pydantic model to the cache as JSON bytes. Best-effort: a backend error
    is logged and skipped (a failed cache write is never fatal)."""
    try:
        await cache.set(key, value.model_dump_json().encode(), ttl_seconds=ttl_seconds)
    except Exception as exc:  # noqa: BLE001 — best-effort L1: a failed write is a no-op
        log.warning("cache set(%s) failed; skipping cache write: %r", key, exc)


class MemoryCache:
    """In-process LRU cache with per-entry TTL. Single-threaded asyncio use → no
    locking (no awaits between check and mutate). `clock` is injectable for tests."""

    def __init__(
        self, max_entries: int = 10_000, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._max = max_entries
        self._clock = clock
        self._store: OrderedDict[str, tuple[bytes, float]] = OrderedDict()

    async def get(self, key: str) -> bytes | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if self._clock() >= expires_at:
            del self._store[key]
            return None
        self._store.move_to_end(key)  # mark most-recently-used
        return value

    async def set(self, key: str, value: bytes, *, ttl_seconds: int) -> None:
        self._store[key] = (value, self._clock() + ttl_seconds)
        self._store.move_to_end(key)
        while len(self._store) > self._max:
            self._store.popitem(last=False)  # evict least-recently-used

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def aclose(self) -> None:
        self._store.clear()


class MemcachedCache:
    """memcached adapter (text protocol via aiomcache). `aiomcache` is imported lazily
    so it's only a dependency when this backend is actually selected.

    **Best-effort by design** (ADR-0033): the L1 cache is an accelerator in front of the
    durable DB tier, never a hard dependency. Every operation is bounded by a timeout and
    swallows transport errors — a get degrades to a miss, a set/delete to a no-op — so an
    unreachable or slow memcached degrades to L2 instead of failing the request (mirroring
    the source-outage handling in `application/market.py`)."""

    def __init__(self, addr: str, *, timeout_seconds: float = 1.0) -> None:
        import aiomcache  # noqa: PLC0415 — lazy: only needed for this backend

        self._timeout = timeout_seconds
        host, _, port = addr.partition(":")
        self._client = aiomcache.Client(host, int(port or 11211))

    async def get(self, key: str) -> bytes | None:
        try:
            return await asyncio.wait_for(
                self._client.get(key.encode()), self._timeout
            )
        except Exception as exc:  # noqa: BLE001 — best-effort L1: never fail the request
            log.warning("memcached get(%s) failed; treating as miss: %r", key, exc)
            return None

    async def set(self, key: str, value: bytes, *, ttl_seconds: int) -> None:
        # Clamp to memcached's relative-TTL window (0/over-30-days change meaning).
        exptime = max(1, min(ttl_seconds, _MAX_EXPTIME))
        try:
            await asyncio.wait_for(
                self._client.set(key.encode(), value, exptime=exptime), self._timeout
            )
        except Exception as exc:  # noqa: BLE001 — best-effort L1: never fail the request
            log.warning("memcached set(%s) failed; skipping cache write: %r", key, exc)

    async def delete(self, key: str) -> None:
        try:
            await asyncio.wait_for(
                self._client.delete(key.encode()), self._timeout
            )
        except Exception as exc:  # noqa: BLE001 — best-effort L1: never fail the request
            log.warning("memcached delete(%s) failed: %r", key, exc)

    async def aclose(self) -> None:
        try:
            await self._client.close()
        except Exception as exc:  # noqa: BLE001 — teardown must not raise
            log.warning("memcached close failed: %r", exc)


def build_cache(settings: Settings) -> Cache:
    """Construct the configured cache backend (ADR-0033). Swapping backends is this
    one switch plus the `BUYBACK_CACHE_BACKEND` env var — no call-site changes."""
    if settings.cache_backend == "memcached":
        return MemcachedCache(
            settings.memcached_addr,
            timeout_seconds=settings.memcached_timeout_seconds,
        )
    return MemoryCache(settings.cache_max_entries)


def get_cache(request: Request) -> Cache:
    """FastAPI dependency: the process-wide cache built at startup (see app lifespan)."""
    return request.app.state.cache
