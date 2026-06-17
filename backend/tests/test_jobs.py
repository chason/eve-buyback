"""Background-job wiring (ADR-0034): the lifespan scheduler setup
(`main._start_market_refresh`) and the interface adapter (`jobs.run_market_refresh`).

The use case itself (`refresh_due_prices`) is covered in test_market_refresh.py; here we
test the glue that keeps the recurring job alive — the enable flag, the scheduler job's
parameters, and the top-level guard that must swallow any failure so the job keeps
firing.
"""

from datetime import datetime
from types import SimpleNamespace

from app.application import market_refresh
from app.config import get_settings
from app.interface import jobs
from app.main import _start_market_refresh


def _fake_app(cache=None) -> SimpleNamespace:
    # run_market_refresh only stores `http` on the plugin clients and reads `cache`; with
    # the use case stubbed it never touches them, so stand-ins are enough.
    return SimpleNamespace(state=SimpleNamespace(http=object(), cache=cache))


# --- lifespan scheduler setup (_start_market_refresh) ---


def test_start_market_refresh_returns_none_when_disabled():
    settings = get_settings().model_copy(
        update={"market_background_refresh_enabled": False}
    )
    assert _start_market_refresh(SimpleNamespace(), settings) is None


async def test_start_market_refresh_configures_the_job_when_enabled():
    settings = get_settings().model_copy(
        update={
            "market_background_refresh_enabled": True,
            "market_refresh_interval_seconds": 600,
            "market_refresh_initial_delay_seconds": 30,
        }
    )

    scheduler = _start_market_refresh(SimpleNamespace(), settings)
    try:
        assert scheduler is not None
        job = scheduler.get_job("market_refresh")
        assert job is not None
        assert job.trigger.interval.total_seconds() == 600
        assert job.max_instances == 1  # never overlap a slow run with the next tick
        assert job.coalesce is True  # collapse missed ticks into one
        # First run is deferred so a cold deploy warms soon without hammering ESI at boot.
        assert job.next_run_time is not None
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
