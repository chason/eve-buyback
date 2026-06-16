"""The pluggable cache plugin (ADR-0033): MemoryCache semantics, the model helpers,
the safe-key contract, the backend factory, and the memcached adapter wiring."""

import asyncio
import hashlib

from pydantic import BaseModel

from app.config import Settings
from app.plugins.cache import (
    _MAX_EXPTIME,
    MemcachedCache,
    MemoryCache,
    build_cache,
    get_model,
    safe_key,
    set_model,
)


class _Clock:
    """Controllable monotonic clock for TTL tests (no sleeping)."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


# --- MemoryCache ---


async def test_memory_get_set_delete_roundtrip():
    cache = MemoryCache()
    assert await cache.get("k") is None
    await cache.set("k", b"value", ttl_seconds=60)
    assert await cache.get("k") == b"value"
    await cache.delete("k")
    assert await cache.get("k") is None


async def test_memory_ttl_expiry():
    clock = _Clock()
    cache = MemoryCache(clock=clock)
    await cache.set("k", b"v", ttl_seconds=10)
    clock.t += 9
    assert await cache.get("k") == b"v"  # still fresh
    clock.t += 2  # now 11s elapsed > 10s ttl
    assert await cache.get("k") is None  # expired, and evicted


async def test_memory_lru_eviction():
    cache = MemoryCache(max_entries=2)
    await cache.set("a", b"1", ttl_seconds=60)
    await cache.set("b", b"2", ttl_seconds=60)
    await cache.get("a")  # touch 'a' → 'b' is now least-recently-used
    await cache.set("c", b"3", ttl_seconds=60)  # over capacity → evict LRU ('b')
    assert await cache.get("a") == b"1"
    assert await cache.get("c") == b"3"
    assert await cache.get("b") is None


# --- safe_key ---


def test_safe_key_passthrough_when_clean():
    assert safe_key("mp", "60003760", 34) == "mp:60003760:34"


def test_safe_key_hashes_unsafe_or_overlong():
    spaced = safe_key("has space")
    assert spaced == "h:" + hashlib.sha1(b"has space").hexdigest()
    long = "x" * 300
    assert safe_key(long) == "h:" + hashlib.sha1(long.encode()).hexdigest()


# --- model helpers ---


class _Sample(BaseModel):
    a: int
    b: str


async def test_get_set_model_roundtrip_and_misses():
    cache = MemoryCache()
    assert await get_model(cache, "k", _Sample) is None  # miss
    await set_model(cache, "k", _Sample(a=1, b="x"), ttl_seconds=60)
    got = await get_model(cache, "k", _Sample)
    assert got == _Sample(a=1, b="x")
    # Corrupt bytes (e.g. a schema change) read as a miss, never an exception.
    await cache.set("k", b"not json", ttl_seconds=60)
    assert await get_model(cache, "k", _Sample) is None


# --- factory + memcached adapter ---


def test_build_cache_selects_backend():
    mem = build_cache(Settings(cache_backend="memory", _env_file=None))
    assert isinstance(mem, MemoryCache)
    mc = build_cache(
        Settings(cache_backend="memcached", memcached_addr="localhost:11211",
                 _env_file=None)
    )
    assert isinstance(mc, MemcachedCache)  # constructs without connecting


class _FakeMC:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def get(self, key):
        self.calls.append(("get", key))
        return b"cached"

    async def set(self, key, value, exptime):
        self.calls.append(("set", key, value, exptime))

    async def delete(self, key):
        self.calls.append(("delete", key))

    async def close(self):
        self.calls.append(("close",))


async def test_memcached_adapter_encodes_keys_and_passes_ttl():
    cache = MemcachedCache("localhost:11211")
    fake = _FakeMC()
    cache._client = fake  # swap the real aiomcache client for a recorder

    assert await cache.get("k") == b"cached"
    await cache.set("k", b"v", ttl_seconds=30)
    await cache.delete("k")
    await cache.aclose()

    assert fake.calls == [
        ("get", b"k"),
        ("set", b"k", b"v", 30),  # bytes key + ttl as exptime
        ("delete", b"k"),
        ("close",),
    ]


async def test_memcached_clamps_ttl_to_relative_window():
    # memcached reads exptime 0 as "never" and > 30 days as an absolute timestamp;
    # the adapter must clamp into the 1..30-day relative window.
    cache = MemcachedCache("localhost:11211")
    fake = _FakeMC()
    cache._client = fake
    await cache.set("a", b"v", ttl_seconds=0)
    await cache.set("b", b"v", ttl_seconds=10**9)
    exptimes = [c[3] for c in fake.calls if c[0] == "set"]
    assert exptimes == [1, _MAX_EXPTIME]


class _FailingMC:
    async def get(self, key):
        raise ConnectionRefusedError("memcached down")

    async def set(self, key, value, exptime):
        raise ConnectionRefusedError("memcached down")

    async def delete(self, key):
        raise ConnectionRefusedError("memcached down")

    async def close(self):
        raise ConnectionRefusedError("memcached down")


async def test_memcached_best_effort_swallows_backend_errors():
    # An unreachable memcached must degrade to a miss / no-op, never raise.
    cache = MemcachedCache("localhost:11211")
    cache._client = _FailingMC()
    assert await cache.get("k") is None  # miss
    await cache.set("k", b"v", ttl_seconds=30)  # no-op, no raise
    await cache.delete("k")  # no-op, no raise
    await cache.aclose()  # best-effort, no raise


class _HangingMC:
    async def get(self, key):
        await asyncio.Event().wait()  # never resolves

    async def set(self, key, value, exptime):
        await asyncio.Event().wait()

    async def delete(self, key):
        await asyncio.Event().wait()

    async def close(self):
        pass


async def test_memcached_times_out_to_miss():
    # A slow/hung node must not stall the caller — the per-op timeout degrades to miss.
    cache = MemcachedCache("localhost:11211", timeout_seconds=0.05)
    cache._client = _HangingMC()
    assert await cache.get("k") is None
    await cache.set("k", b"v", ttl_seconds=30)  # times out → no-op, no raise


def test_build_cache_threads_memcached_timeout():
    cache = build_cache(
        Settings(
            cache_backend="memcached", memcached_addr="localhost:11211",
            memcached_timeout_seconds=2.5, environment="development", _env_file=None,
        )
    )
    assert isinstance(cache, MemcachedCache)
    assert cache._timeout == 2.5
