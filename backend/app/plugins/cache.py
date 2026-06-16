"""A small pluggable key-value cache (ADR-0033).

A **plugin** (outside-resource gateway) exposing a backend-agnostic port. The port is
shaped to memcached's lowest common denominator — string keys, opaque `bytes` values,
a per-key TTL, and **no** enumerate/clear — so the in-memory default can't grow a habit
the memcached adapter couldn't keep, and swapping backends is a config change with no
call-site edits.

Used today as an L1 tier in front of the durable `market_prices` DB cache
(`application/market.py`); the port itself knows nothing about market data.
"""

import hashlib
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Protocol

from fastapi import Request
from pydantic import BaseModel

from app.config import Settings

# memcached limits: keys ≤ 250 bytes with no whitespace/control chars; values ≤ 1 MiB.
_MAX_KEY_LEN = 250


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
    """Read a Pydantic model from the cache (None on miss/corrupt entry)."""
    raw = await cache.get(key)
    if raw is None:
        return None
    try:
        return model_cls.model_validate_json(raw)
    except ValueError:
        return None  # a schema change can leave undeserializable bytes — treat as miss


async def set_model(
    cache: Cache, key: str, value: BaseModel, *, ttl_seconds: int
) -> None:
    """Write a Pydantic model to the cache as JSON bytes."""
    await cache.set(key, value.model_dump_json().encode(), ttl_seconds=ttl_seconds)


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
    """memcached adapter (RFC-ish text protocol via aiomcache). `aiomcache` is
    imported lazily so it's only a dependency when this backend is actually selected."""

    def __init__(self, addr: str) -> None:
        import aiomcache  # noqa: PLC0415 — lazy: only needed for this backend

        host, _, port = addr.partition(":")
        self._client = aiomcache.Client(host, int(port or 11211))

    async def get(self, key: str) -> bytes | None:
        return await self._client.get(key.encode())

    async def set(self, key: str, value: bytes, *, ttl_seconds: int) -> None:
        await self._client.set(key.encode(), value, exptime=ttl_seconds)

    async def delete(self, key: str) -> None:
        await self._client.delete(key.encode())

    async def aclose(self) -> None:
        await self._client.close()


def build_cache(settings: Settings) -> Cache:
    """Construct the configured cache backend (ADR-0033). Swapping backends is this
    one switch plus the `BUYBACK_CACHE_BACKEND` env var — no call-site changes."""
    if settings.cache_backend == "memcached":
        return MemcachedCache(settings.memcached_addr)
    return MemoryCache(settings.cache_max_entries)


def get_cache(request: Request) -> Cache:
    """FastAPI dependency: the process-wide cache built at startup (see app lifespan)."""
    return request.app.state.cache
