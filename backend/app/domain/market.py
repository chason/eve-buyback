"""Pure market-data helpers (no I/O)."""

from datetime import datetime, timedelta


def is_fresh(fetched_at: datetime, *, now: datetime, ttl_seconds: int) -> bool:
    """True if a cached price fetched at `fetched_at` is still within its TTL."""
    return now - fetched_at < timedelta(seconds=ttl_seconds)
