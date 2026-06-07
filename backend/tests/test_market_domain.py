from datetime import UTC, datetime, timedelta

from app.domain.market import is_fresh

NOW = datetime(2026, 6, 7, 12, 0, 0, tzinfo=UTC)
TTL = 3600


def test_fresh_within_ttl():
    assert is_fresh(NOW - timedelta(seconds=10), now=NOW, ttl_seconds=TTL) is True


def test_stale_past_ttl():
    assert is_fresh(NOW - timedelta(seconds=TTL + 1), now=NOW, ttl_seconds=TTL) is False


def test_exactly_ttl_is_stale():
    # The boundary is exclusive: age == ttl counts as stale.
    assert is_fresh(NOW - timedelta(seconds=TTL), now=NOW, ttl_seconds=TTL) is False
