"""Background-job wiring (ADR-0034, ADR-0036): the lifespan scheduler setup
(`main._start_scheduler`) and the interface adapter (`jobs.run_market_refresh`).

The use cases themselves are covered in test_market_refresh.py / test_corp_roster.py; here
we test the glue that keeps the recurring jobs alive — the enable flags, the scheduler jobs'
parameters, and the top-level guard that must swallow any failure so the job keeps firing.
"""

from datetime import datetime
from types import SimpleNamespace

from app.application import market_refresh
from app.config import get_settings
from app.interface import jobs
from app.main import _start_scheduler


def _fake_app(cache=None) -> SimpleNamespace:
    # run_market_refresh only stores `http`/`esi_semaphore` on the plugin clients and
    # reads `cache`; with the use case stubbed it never touches them, so stand-ins are
    # enough. `esi_semaphore` mirrors the lifespan (ADR-0035).
    return SimpleNamespace(
        state=SimpleNamespace(http=object(), esi_semaphore=None, cache=cache)
    )


# --- lifespan scheduler setup (_start_scheduler) ---


def test_start_scheduler_returns_none_when_all_jobs_disabled():
    settings = get_settings().model_copy(
        update={
            "market_background_refresh_enabled": False,
            "roster_background_refresh_enabled": False,
            "contracts_background_refresh_enabled": False,
            "payments_background_refresh_enabled": False,
        }
    )
    assert _start_scheduler(SimpleNamespace(), settings) is None


async def test_start_scheduler_configures_enabled_jobs():
    settings = get_settings().model_copy(
        update={
            "market_background_refresh_enabled": True,
            "market_refresh_interval_seconds": 600,
            "market_refresh_initial_delay_seconds": 30,
            "roster_background_refresh_enabled": True,
            "roster_refresh_interval_seconds": 86400,
            "roster_refresh_initial_delay_seconds": 60,
            "contracts_background_refresh_enabled": True,
            "contracts_refresh_interval_seconds": 900,
            "contracts_refresh_initial_delay_seconds": 90,
            "payments_background_refresh_enabled": True,
            "payments_refresh_interval_seconds": 1800,
            "payments_refresh_initial_delay_seconds": 120,
        }
    )

    scheduler = _start_scheduler(SimpleNamespace(), settings)
    try:
        assert scheduler is not None
        market = scheduler.get_job("market_refresh")
        assert market is not None
        assert market.trigger.interval.total_seconds() == 600
        assert market.max_instances == 1  # never overlap a slow run with the next tick
        assert market.coalesce is True  # collapse missed ticks into one
        assert market.next_run_time is not None  # deferred first run
        roster = scheduler.get_job("roster_refresh")
        assert roster is not None
        assert roster.trigger.interval.total_seconds() == 86400
        contracts = scheduler.get_job("contracts_refresh")
        assert contracts is not None
        assert contracts.trigger.interval.total_seconds() == 900
        assert contracts.max_instances == 1
        assert contracts.coalesce is True
        payments = scheduler.get_job("payments_reconcile")
        assert payments is not None
        assert payments.trigger.interval.total_seconds() == 1800
        assert payments.max_instances == 1
        assert payments.coalesce is True
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)


async def test_start_scheduler_omits_a_disabled_job():
    settings = get_settings().model_copy(
        update={
            "market_background_refresh_enabled": False,
            "roster_background_refresh_enabled": True,
            "contracts_background_refresh_enabled": False,
        }
    )
    scheduler = _start_scheduler(SimpleNamespace(), settings)
    try:
        assert scheduler is not None
        assert scheduler.get_job("market_refresh") is None
        assert scheduler.get_job("roster_refresh") is not None
        assert scheduler.get_job("contracts_refresh") is None
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)


# --- adapter guard (run_market_refresh) ---


async def test_run_market_refresh_survives_a_use_case_failure(monkeypatch):
    calls = {"n": 0}

    async def boom(*args, **kwargs):
        calls["n"] += 1
        raise RuntimeError("ESI exploded")

    monkeypatch.setattr(market_refresh, "refresh_due_prices", boom)

    # Must NOT raise — a recurring job that propagates an exception can stop firing.
    result = await jobs.run_market_refresh(_fake_app())

    assert result is None
    assert calls["n"] == 1  # reached the use case, then swallowed the failure


async def test_run_market_refresh_invokes_the_use_case_with_wired_deps(monkeypatch):
    seen: dict = {}

    async def spy(session, *, esi_market, sso, cipher, cache, settings, now):
        seen.update(
            cache=cache, settings=settings, now=now,
            esi=esi_market, sso=sso, cipher=cipher,
        )
        return market_refresh.RefreshSummary()

    monkeypatch.setattr(market_refresh, "refresh_due_prices", spy)

    await jobs.run_market_refresh(_fake_app(cache="sentinel-cache"))

    # The adapter threaded app state + settings through to the use case…
    assert seen["cache"] == "sentinel-cache"
    assert seen["settings"] is get_settings()
    assert isinstance(seen["now"], datetime)
    # …and built the plugin clients it owns.
    assert seen["esi"] is not None
    assert seen["sso"] is not None
    assert seen["cipher"] is not None
