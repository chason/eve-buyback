"""Background market refresh (ADR-0034): the scheduled use case that keeps non-Fuzzwork
hub prices warm — ESI-region hot-set renewal + full structure-book pre-warm, with
graceful per-hub degrade."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.application import market_refresh
from app.config import get_settings
from app.data.db import SessionLocal
from app.data.records import MarketPriceRecord
from app.data.repositories import buyback_config as config_repo
from app.data.repositories import characters as characters_repo
from app.data.repositories import corporations as corporations_repo
from app.data.repositories import prices as prices_repo
from app.data.repositories import structure_tokens as tokens_repo
from app.domain.aggregates import OrderBookAggregate, SideAggregate
from app.plugins.cache import MemoryCache, get_model, safe_key
from app.plugins.sso import OAuthToken
from app.plugins.token_cipher import TokenCipher

NOW = datetime(2026, 6, 7, 12, 0, 0, tzinfo=UTC)
# ttl=3600, interval=600 ⇒ refresh cutoff = NOW - 3000s. DUE predates it, FRESH doesn't.
DUE_AT = NOW - timedelta(seconds=3600)
FRESH_AT = NOW - timedelta(seconds=60)

REGION_HUB = "60012345"  # non-Fuzzwork NPC station
REGION_ID = 10000002
STRUCT_HUB = "1035000000001"
FUZZ_HUB = "60003760"  # Jita — must never be refreshed


def _book(buy: str, sell: str) -> OrderBookAggregate:
    def side(v: str) -> SideAggregate:
        m = Decimal(v)
        return SideAggregate(
            weighted_average=m, max=m, min=m, median=m, percentile=m,
            volume=Decimal("100"), order_count=5,
        )

    return OrderBookAggregate(buy=side(buy), sell=side(sell))


def _price_row(type_id: int, marker: str, fetched_at: datetime) -> dict:
    m = Decimal(marker)
    return {
        "type_id": type_id,
        "buy_weighted_average": m, "buy_max": m, "buy_min": m,
        "buy_median": m, "buy_percentile": m, "buy_volume": Decimal("1"),
        "buy_order_count": 1,
        "sell_weighted_average": m, "sell_max": m, "sell_min": m,
        "sell_median": m, "sell_percentile": m, "sell_volume": Decimal("1"),
        "sell_order_count": 1,
        "fetched_at": fetched_at,
    }


class FakeEsi:
    """Records the refresh calls so we can assert exactly what got fetched."""

    def __init__(self, *, region=None, structure=None, region_error=None,
                 structure_error=None):
        self.region = region or {}
        self.structure = structure or {}
        self.region_error = region_error
        self.structure_error = structure_error
        self.region_type_ids: list[list[int]] = []
        self.structure_calls = 0

    async def get_region_aggregates(self, *, region_id, station_id, type_ids):
        self.region_type_ids.append(sorted(type_ids))
        if self.region_error is not None:
            raise self.region_error
        return {t: self.region[t] for t in type_ids if t in self.region}

    async def get_all_structure_aggregates(self, *, structure_id, access_token):
        self.structure_calls += 1
        if self.structure_error is not None:
            raise self.structure_error
        return dict(self.structure)


class FakeSso:
    def __init__(self) -> None:
        self.refresh_tokens_seen: list[str] = []

    async def refresh_access_token(self, refresh_token: str) -> OAuthToken:
        self.refresh_tokens_seen.append(refresh_token)
        # Same refresh token back → no rotation/update path in get_structure_access_token.
        return OAuthToken(access_token="access-tok", refresh_token=refresh_token)


def _cipher() -> TokenCipher:
    return TokenCipher(get_settings().token_encryption_key)


async def _make_corp(eve_id: int, name: str = "Corp"):
    async with SessionLocal() as session:
        corp = await corporations_repo.create_corporation(
            session, eve_corporation_id=eve_id, name=name,
            ceo_character_id=eve_id + 1, registered_by_character_id=eve_id + 1,
        )
        await session.commit()
        return corp


async def _set_config(corp_id, *, hub_id, kind="npc_station", region_id=None):
    async with SessionLocal() as session:
        await config_repo.upsert_config(
            session, corporation_id=corp_id, market_hub_id=hub_id,
            market_hub_kind=kind, market_region_id=region_id,
            default_basis="buy", default_percentage=Decimal("90"),
            aggregate_field="percentile",
        )
        await session.commit()


async def _seed_prices(hub_id: str, rows: list[dict]) -> None:
    async with SessionLocal() as session:
        await prices_repo.upsert_prices(session, hub_id=hub_id, rows=rows)
        await session.commit()


async def _grant_token(corp_id, *, eve_char_id=777, failed_at=None, refresh="rt"):
    async with SessionLocal() as session:
        char = await characters_repo.upsert_character(
            session, eve_character_id=eve_char_id, name=f"Char {eve_char_id}"
        )
        await tokens_repo.upsert_token(
            session, corporation_id=corp_id, character_id=char.id,
            character_eve_id=eve_char_id, character_name=f"Char {eve_char_id}",
            encrypted_refresh_token=_cipher().encrypt(refresh),
            scopes="esi-markets.structure_markets.v1",
        )
        if failed_at is not None:
            await tokens_repo.mark_failed(session, corporation_id=corp_id, at=failed_at)
        await session.commit()


async def _run(esi, *, cache=None, sso=None, now=NOW):
    async with SessionLocal() as session:
        return await market_refresh.refresh_due_prices(
            session, esi_market=esi, sso=sso or FakeSso(), cipher=_cipher(),
            cache=cache, settings=get_settings(), now=now,
        )


async def _prices(hub_id: str, type_ids: list[int]) -> dict[int, Decimal]:
    async with SessionLocal() as session:
        rows = await prices_repo.get_prices(session, hub_id=hub_id, type_ids=type_ids)
    return {r.type_id: r.buy_percentile for r in rows}


# --- ESI-region hot set ---


async def test_region_refreshes_only_the_due_hot_set():
    corp = await _make_corp(98000001)
    await _set_config(corp.id, hub_id=REGION_HUB, region_id=REGION_ID)
    # 34 is due (old), 35 is fresh (recent) — only 34 should be re-fetched.
    await _seed_prices(REGION_HUB, [
        _price_row(34, "1.0", DUE_AT),
        _price_row(35, "2.0", FRESH_AT),
    ])
    esi = FakeEsi(region={34: _book(buy="50.0", sell="60.0")})

    summary = await _run(esi)

    assert esi.region_type_ids == [[34]]  # only the due id was fetched
    assert esi.structure_calls == 0
    prices = await _prices(REGION_HUB, [34, 35])
    assert prices[34] == Decimal("50.0")  # renewed
    assert prices[35] == Decimal("2.0")   # untouched fresh row
    assert summary.hubs_refreshed == 1 and summary.types_written == 1


async def test_region_nothing_due_makes_no_call():
    corp = await _make_corp(98000002)
    await _set_config(corp.id, hub_id=REGION_HUB, region_id=REGION_ID)
    await _seed_prices(REGION_HUB, [_price_row(34, "2.0", FRESH_AT)])  # all fresh
    esi = FakeEsi(region={34: _book(buy="99.0", sell="99.0")})

    summary = await _run(esi)

    assert esi.region_type_ids == []
    assert summary.hubs_refreshed == 0


async def test_region_promotes_into_l1():
    corp = await _make_corp(98000003)
    await _set_config(corp.id, hub_id=REGION_HUB, region_id=REGION_ID)
    await _seed_prices(REGION_HUB, [_price_row(34, "1.0", DUE_AT)])
    cache = MemoryCache()
    esi = FakeEsi(region={34: _book(buy="50.0", sell="60.0")})

    await _run(esi, cache=cache)

    promoted = await get_model(
        cache, safe_key("mp", REGION_HUB, 34), MarketPriceRecord
    )
    assert promoted is not None and promoted.buy_percentile == Decimal("50.0")


# --- Structure full pre-warm ---


async def test_structure_prewarms_entire_book():
    corp = await _make_corp(98000004)
    await _set_config(corp.id, hub_id=STRUCT_HUB, kind="structure")
    await _grant_token(corp.id)
    # No prices cached yet → due → the WHOLE book is cached, incl. never-appraised types.
    esi = FakeEsi(structure={
        34: _book(buy="3.0", sell="4.0"),
        35: _book(buy="5.0", sell="6.0"),
        36: _book(buy="7.0", sell="8.0"),
    })

    summary = await _run(esi)

    assert esi.structure_calls == 1
    prices = await _prices(STRUCT_HUB, [34, 35, 36])
    assert prices == {34: Decimal("3.0"), 35: Decimal("5.0"), 36: Decimal("7.0")}
    assert summary.types_written == 3


async def test_structure_not_due_skips_fetch():
    corp = await _make_corp(98000005)
    await _set_config(corp.id, hub_id=STRUCT_HUB, kind="structure")
    await _grant_token(corp.id)
    # A freshly-cached row dates the last full refresh → not due.
    await _seed_prices(STRUCT_HUB, [_price_row(34, "3.0", FRESH_AT)])
    esi = FakeEsi(structure={34: _book(buy="99.0", sell="99.0")})

    summary = await _run(esi)

    assert esi.structure_calls == 0
    assert summary.hubs_refreshed == 0


async def test_structure_empty_book_is_not_refetched_within_the_window():
    # #70: a structure whose book comes back empty writes no price rows, but the refresh
    # marker still advances — so it isn't re-fetched again until the window lapses
    # (previously latest_fetched_at stayed null and the whole book re-fetched forever).
    corp = await _make_corp(98000017)
    await _set_config(corp.id, hub_id=STRUCT_HUB, kind="structure")
    await _grant_token(corp.id, eve_char_id=831)
    esi = FakeEsi(structure={})  # empty book — a successful fetch with no orders

    summary = await _run(esi)
    assert esi.structure_calls == 1
    assert summary.hubs_refreshed == 0  # nothing written
    assert await _prices(STRUCT_HUB, [34]) == {}

    # Same window → the marker suppresses the re-fetch.
    await _run(esi)
    assert esi.structure_calls == 1

    # Once the window lapses, it fetches again.
    await _run(esi, now=NOW + timedelta(hours=2))
    assert esi.structure_calls == 2


async def test_structure_missing_token_skips_gracefully():
    corp = await _make_corp(98000006)
    await _set_config(corp.id, hub_id=STRUCT_HUB, kind="structure")
    # No token granted for this corp → can't fetch → skip, no raise.
    esi = FakeEsi(structure={34: _book(buy="3.0", sell="4.0")})

    summary = await _run(esi)

    assert esi.structure_calls == 0
    assert summary.hubs_refreshed == 0
    assert await _prices(STRUCT_HUB, [34]) == {}


async def test_structure_picks_a_healthy_corp_token_over_a_failed_one():
    # Two corps reference the same structure; corp A's token is flagged failed, corp B's
    # is healthy → B's token is used (skip-failed, most-recent-first ordering).
    corp_a = await _make_corp(98000007, name="A")
    corp_b = await _make_corp(98000008, name="B")
    await _set_config(corp_a.id, hub_id=STRUCT_HUB, kind="structure")
    await _set_config(corp_b.id, hub_id=STRUCT_HUB, kind="structure")
    await _grant_token(corp_a.id, eve_char_id=701, failed_at=NOW)
    await _grant_token(corp_b.id, eve_char_id=702)
    esi = FakeEsi(structure={34: _book(buy="3.0", sell="4.0")})

    summary = await _run(esi)

    assert esi.structure_calls == 1  # one corp's token sufficed (only fetched once)
    assert summary.types_written == 1


async def test_structure_prefers_least_recently_authorized_healthy_corp():
    # Two healthy corps on one structure → the OLDER grant is tried first (and, with
    # first-success-wins, is the only one used). Proves the SQL health ordering.
    older = await _make_corp(98000012, name="Older")
    newer = await _make_corp(98000013, name="Newer")
    await _set_config(older.id, hub_id=STRUCT_HUB, kind="structure")
    await _set_config(newer.id, hub_id=STRUCT_HUB, kind="structure")
    await _grant_token(older.id, eve_char_id=801, refresh="rt-older")
    await _grant_token(newer.id, eve_char_id=802, refresh="rt-newer")
    esi = FakeEsi(structure={34: _book(buy="3.0", sell="4.0")})
    sso = FakeSso()

    await _run(esi, sso=sso)

    assert sso.refresh_tokens_seen == ["rt-older"]


async def test_structure_token_selection_rotates_across_corps():
    # #88: the fetching token rotates least-recently-used first. Cycle 1 uses the older
    # grant; once stamped, cycle 2 (a later `now`, so the structure is due again) moves
    # to the other corp.
    a = await _make_corp(98000014, name="A")
    b = await _make_corp(98000015, name="B")
    await _set_config(a.id, hub_id=STRUCT_HUB, kind="structure")
    await _set_config(b.id, hub_id=STRUCT_HUB, kind="structure")
    await _grant_token(a.id, eve_char_id=811, refresh="rt-a")  # older grant
    await _grant_token(b.id, eve_char_id=812, refresh="rt-b")
    esi = FakeEsi(structure={34: _book(buy="3.0", sell="4.0")})

    sso1 = FakeSso()
    await _run(esi, sso=sso1)  # both never used → oldest (A) wins
    assert sso1.refresh_tokens_seen == ["rt-a"]

    sso2 = FakeSso()
    # Two hours on so the structure is due again; A was just used → B is now LRU.
    await _run(esi, sso=sso2, now=NOW + timedelta(hours=2))
    assert sso2.refresh_tokens_seen == ["rt-b"]


# --- isolation + degrade across hubs ---


async def test_one_hub_failure_does_not_abort_the_others():
    # A structure that 403s should be skipped while a region hub still refreshes.
    sc = await _make_corp(98000009, name="S")
    rc = await _make_corp(98000010, name="R")
    await _set_config(sc.id, hub_id=STRUCT_HUB, kind="structure")
    await _grant_token(sc.id)
    await _set_config(rc.id, hub_id=REGION_HUB, region_id=REGION_ID)
    await _seed_prices(REGION_HUB, [_price_row(34, "1.0", DUE_AT)])

    from app.plugins.esi_market import StructureAccessDenied
    esi = FakeEsi(
        region={34: _book(buy="50.0", sell="60.0")},
        structure_error=StructureAccessDenied(),
    )

    summary = await _run(esi)  # must not raise

    assert (await _prices(REGION_HUB, [34]))[34] == Decimal("50.0")  # region still refreshed
    assert summary.hubs_refreshed == 1  # only the region hub


async def test_structure_access_denied_flags_token_then_recovery_clears_it():
    # #68: a 403 on the structure fetch flags the token (so the manager sees a
    # "re-authorize" warning instead of a silent failure); a later successful fetch
    # clears the flag (self-heal, no re-auth needed).
    from app.plugins.esi_market import StructureAccessDenied

    corp = await _make_corp(98000016)
    await _set_config(corp.id, hub_id=STRUCT_HUB, kind="structure")
    await _grant_token(corp.id, eve_char_id=821)

    await _run(FakeEsi(structure_error=StructureAccessDenied()))
    async with SessionLocal() as session:
        token = await tokens_repo.get_for_corp(session, corp.id)
    assert token.last_refresh_failed_at is not None  # flagged

    # A successful fetch later (structure is due again) clears the failure.
    await _run(
        FakeEsi(structure={34: _book(buy="3.0", sell="4.0")}),
        now=NOW + timedelta(hours=2),
    )
    async with SessionLocal() as session:
        token = await tokens_repo.get_for_corp(session, corp.id)
    assert token.last_refresh_failed_at is None  # self-healed
    assert token.last_used_at is not None


async def test_fuzzwork_hub_is_never_refreshed():
    corp = await _make_corp(98000011)
    await _set_config(corp.id, hub_id=FUZZ_HUB)  # Jita — Fuzzwork covers it
    await _seed_prices(FUZZ_HUB, [_price_row(34, "1.0", DUE_AT)])  # stale, but Fuzzwork
    esi = FakeEsi(region={34: _book(buy="50.0", sell="60.0")})

    summary = await _run(esi)

    assert esi.region_type_ids == []  # Fuzzwork hubs are out of scope for the ESI refresh
    assert summary.hubs_refreshed == 0
