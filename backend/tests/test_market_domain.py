from datetime import UTC, datetime, timedelta

from app.domain.market import is_fresh, refresh_cutoff

NOW = datetime(2026, 6, 7, 12, 0, 0, tzinfo=UTC)
TTL = 3600


def test_fresh_within_ttl():
    assert is_fresh(NOW - timedelta(seconds=10), now=NOW, ttl_seconds=TTL) is True


def test_stale_past_ttl():
    assert is_fresh(NOW - timedelta(seconds=TTL + 1), now=NOW, ttl_seconds=TTL) is False


def test_exactly_ttl_is_stale():
    # The boundary is exclusive: age == ttl counts as stale.
    assert is_fresh(NOW - timedelta(seconds=TTL), now=NOW, ttl_seconds=TTL) is False


def test_refresh_cutoff_leaves_one_interval_of_lead():
    # Renew anything that would expire before the next cycle: cutoff = now - (ttl - interval).
    cutoff = refresh_cutoff(NOW, ttl_seconds=TTL, interval_seconds=600)
    assert cutoff == NOW - timedelta(seconds=3000)


def test_refresh_cutoff_clamps_when_interval_exceeds_ttl():
    # interval ≥ ttl ⇒ no lead time to preserve ⇒ everything due every cycle (cutoff == now).
    assert refresh_cutoff(NOW, ttl_seconds=600, interval_seconds=3600) == NOW
