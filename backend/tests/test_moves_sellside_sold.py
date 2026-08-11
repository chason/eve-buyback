"""#207 / ADR-0049 sell-side pairing, already-sold stock + cost true-up: the
excess end of a "looks like a move" pair can be sale rows still carrying an
estimated cost (the ADR-0045 no-lot fallback and consumed deemed lots).
Confirming NEVER touches the booked sale rows — frozen facts, they stay flagged
estimated — it retires the oldest idle origin lots FIFO for the sold quantity
and books real landed cost minus estimated COGS as ONE aggregate cost true-up
attributed to the move, positive or negative. Retired quantity never re-pairs;
the three excess signals (counted / listed / sold) sum without double
counting."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.application import corp_esi_token as corp_esi_token_app
from app.application import moves as moves_app
from app.application import profit as profit_app
from app.application import reconciliation as recon_app
from app.application import sales as sales_app
from app.application.auth import AuthenticatedUser
from app.data.db import SessionLocal
from app.data.models import MarketPrice
from app.data.repositories import buyback_config as config_repo
from app.data.repositories import corporations as corporations_repo
from app.data.repositories import entitlements as entitlements_repo
from app.data.repositories import expenses as expenses_repo
from app.data.repositories import hangars as hangars_repo
from app.data.repositories import lots as lots_repo
from app.data.repositories import market_orders as orders_repo
from app.data.repositories import move_suggestions as moves_repo
from app.data.repositories import reconciliation as recon_repo
from app.data.repositories import sales as sales_repo
from app.data.repositories import sde as sde_repo
from app.domain.moves import (
    MovePair,
    match_move_pairs,
    sold_cost_estimate,
    unretired_sold,
)
from app.plugins.esi import CharacterInfo, CorporationAsset, CorporationInfo
from app.plugins.sso import OAuthToken, VerifiedCharacter
from app.plugins.token_cipher import get_token_cipher
from tests.helpers import CHAR_ID, CORP_ID

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
JITA = "60003760"
AMARR = "60008494"
DODIXIE = "60011866"  # never a configured hangar in these tests
TRIT = 34


# --- the pure functions ------------------------------------------------------------


def test_sold_evidence_pairs_without_any_other_signal():
    pairs = match_move_pairs(
        shortfalls=[(JITA, TRIT, 100)],
        excesses=[],
        listed=[],
        sold=[(DODIXIE, TRIT, 40)],
    )
    assert pairs == [
        MovePair(
            type_id=TRIT,
            origin_location_id=JITA,
            destination_location_id=DODIXIE,
            qty=40,
            qty_counted=0,
            qty_listed=0,
            qty_sold=40,
        )
    ]


def test_three_signals_sum_toward_the_cap_counted_then_listed_then_sold():
    # 30 counted + 30 listed + 40 sold, only 80 missing: counted fills first
    # (a deemed lot to reverse), then listed (future fills at verified cost),
    # then sold (only the aggregate true-up left to gain).
    pairs = match_move_pairs(
        shortfalls=[(JITA, TRIT, 80)],
        excesses=[(AMARR, TRIT, 30)],
        listed=[(AMARR, TRIT, 30)],
        sold=[(AMARR, TRIT, 40)],
    )
    (pair,) = pairs
    assert (pair.qty, pair.qty_counted, pair.qty_listed, pair.qty_sold) == (
        80, 30, 30, 20,
    )


def test_sold_evidence_at_the_origin_itself_never_pairs():
    assert match_move_pairs(
        shortfalls=[(JITA, TRIT, 100)], excesses=[], sold=[(JITA, TRIT, 40)]
    ) == []


def test_unretired_sold_floors_at_what_confirmations_retired():
    sold = {(DODIXIE, TRIT): 100, (AMARR, TRIT): 50}
    retired = {(DODIXIE, TRIT): 40, (AMARR, TRIT): 50}
    assert unretired_sold(sold, retired) == [(DODIXIE, TRIT, 60)]


def test_sold_cost_estimate_walks_oldest_first_past_the_skip():
    rows = [(10, Decimal("3.60")), (20, Decimal("2.00"))]
    # Skip the 5 oldest units (a prior confirmation covered them), take 15:
    # 5 × 3.60 from the first row + 10 × 2.00 from the second.
    assert sold_cost_estimate(rows, skip=5, take=15) == Decimal("38.00")


# --- sync wiring harness -----------------------------------------------------------


class FakeSso:
    configured = True

    def build_authorize_url(self, *, state, code_challenge, scopes=None):
        return f"https://login.eveonline.com/authorize?state={state}"

    async def exchange_code(self, code, code_verifier):
        return OAuthToken(access_token="a", refresh_token="r")

    async def verify_token(self, access_token):
        return VerifiedCharacter(character_id=CHAR_ID, name="Boss")

    async def refresh_access_token(self, refresh_token):
        return OAuthToken(access_token="fresh", refresh_token=refresh_token)


class MultiHangarEsi:
    """ESI fake: token-connect validation + an assets read spanning multiple
    stations. `hangar` is `(location_id, type_id) → qty` in CorpSAG2."""

    def __init__(self):
        self.hangar: dict[tuple[str, int], int] = {}
        self._item_id = iter(range(7_000_000, 8_000_000))

    async def get_character(self, character_id):
        return CharacterInfo(name="Boss", corporation_id=CORP_ID)

    async def get_character_corporation(self, character_id):
        return CORP_ID

    async def get_corporation(self, corporation_id):
        return CorporationInfo(name="Test Corp", ceo_id=CHAR_ID, ticker="T")

    async def get_character_roles(self, character_id, access_token):
        return []

    async def get_corporation_assets(self, corporation_id, access_token):
        assets = []
        for (loc, tid), qty in self.hangar.items():
            assets.append(
                CorporationAsset(
                    item_id=next(self._item_id), type_id=tid, quantity=qty,
                    location_id=int(loc), location_flag="CorpSAG2",
                )
            )
        return assets


def _user() -> AuthenticatedUser:
    return AuthenticatedUser(
        character_id=CHAR_ID, character_name="Boss", corporation_id=CORP_ID,
        corporation_name="Test Corp", role="ceo", is_director=False,
        corporation_registered=True,
    )


async def _seed(esi: MultiHangarEsi, *, prices: dict[int, str] | None = None):
    """Registered + entitled corp with marked hangars at Jita AND Amarr, Jita
    config, SDE types + stations, cached prices, and a corp ESI token. Dodixie
    is deliberately NOT a hangar — the sell side isn't scoped by the list."""
    async with SessionLocal() as session:
        corp = await corporations_repo.create_corporation(
            session, eve_corporation_id=CORP_ID, name="Test Corp",
            ceo_character_id=CHAR_ID, registered_by_character_id=CHAR_ID,
        )
        await entitlements_repo.upsert(
            session, corporation_id=corp.id, feature="accounting",
            source="admin", expires_at=None,
        )
        await config_repo.upsert_config(
            session, corporation_id=corp.id, market_hub_id=JITA,
            default_basis="buy", default_percentage=90,
            aggregate_field="percentile",
        )
        await hangars_repo.add(
            session, corporation_id=corp.id, location_id=JITA,
            location_name="Jita IV - Moon 4", division=2,
        )
        await hangars_repo.add(
            session, corporation_id=corp.id, location_id=AMARR,
            location_name="Amarr VIII - Emperor Family Academy", division=2,
        )
        await sde_repo.bulk_upsert_types(session, [
            {"type_id": TRIT, "name": "Tritanium", "group_id": 18,
             "market_group_id": 1857, "volume": 0.01, "published": True},
        ])
        await sde_repo.bulk_upsert_stations(session, [
            {"station_id": int(DODIXIE),
             "name": "Dodixie IX - Moon 20 - Federation Navy Assembly Plant",
             "system_name": "Dodixie", "region_id": 10000032},
        ])
        for type_id, buy in (prices or {}).items():
            b = Decimal(buy)
            session.add(MarketPrice(
                hub_id=JITA, type_id=type_id,
                buy_weighted_average=b, buy_max=b, buy_min=b, buy_median=b,
                buy_percentile=b, buy_volume=Decimal(1000), buy_order_count=10,
                sell_weighted_average=b, sell_max=b, sell_min=b, sell_median=b,
                sell_percentile=b, sell_volume=Decimal(1000),
                sell_order_count=10, fetched_at=NOW,
            ))
        await session.commit()
    async with SessionLocal() as session:
        await corp_esi_token_app.complete_corp_esi_authorize(
            session, FakeSso(), esi, code="c", verifier="v",
            user=_user(), cipher=get_token_cipher(),
        )


async def _book_lot(
    type_id: int,
    qty: int,
    location_id: str,
    *,
    unit_cost: str = "5.00",
    acquired_at: datetime = NOW - timedelta(days=30),
):
    async with SessionLocal() as session:
        corp = await corporations_repo.get_by_eve_id(session, CORP_ID)
        lot = await lots_repo.create_lot(
            session, corporation_id=corp.id, item_type_id=type_id, qty=qty,
            unit_purchase_cost=Decimal(unit_cost), acquired_at=acquired_at,
            source="buyback", location_id=location_id,
        )
        await session.commit()
    return lot


async def _sell(
    type_id: int,
    qty: int,
    location_id: str,
    *,
    unit_proceeds: str = "6.10",
    ref: int,
) -> bool:
    """One detected market fill at a location, as the sales ingestion would
    book it — the no-lot fallback fires when the books hold nothing there."""
    async with SessionLocal() as session:
        corp = await corporations_repo.get_by_eve_id(session, CORP_ID)
        unmatched = await sales_app.consume_and_record_sale(
            session, corp.id, type_id=type_id, qty=qty,
            unit_proceeds=Decimal(unit_proceeds), location_id=location_id,
            channel="market", external_ref=ref, sold_at=NOW, now=NOW,
        )
        await session.commit()
    return unmatched


async def _list_orders(rows: list[tuple[str, int, int]]) -> None:
    orders = []
    for i, (loc, tid, qty) in enumerate(rows):
        orders.append({
            "order_id": 9_000_000 + i, "type_id": tid, "is_buy_order": False,
            "price": Decimal("6.10"), "volume_remain": qty,
            "volume_total": qty, "location_id": loc, "wallet_division": 3,
            "issued": NOW,
        })
    async with SessionLocal() as session:
        corp = await corporations_repo.get_by_eve_id(session, CORP_ID)
        await orders_repo.replace_for_corp(
            session, corporation_id=corp.id, orders=orders
        )
        await session.commit()


async def _run(esi: MultiHangarEsi):
    async with SessionLocal() as session:
        return await recon_app.reconcile_hangars(
            session, FakeSso(), esi, corporation_eve_id=CORP_ID,
            cipher=get_token_cipher(), excess_flag_isk=1_000_000_000, now=NOW,
        )


async def _state():
    async with SessionLocal() as session:
        corp = await corporations_repo.get_by_eve_id(session, CORP_ID)
        lots = await lots_repo.open_lots(session, corporation_id=corp.id)
        events = await recon_repo.list_for_corp(session, corporation_id=corp.id)
        suggestions = await moves_repo.list_pending(session, corporation_id=corp.id)
    return lots, events, suggestions


async def _sales_and_expenses():
    async with SessionLocal() as session:
        corp = await corporations_repo.get_by_eve_id(session, CORP_ID)
        sales = await sales_repo.list_for_corp(session, corporation_id=corp.id)
        expenses = await expenses_repo.list_for_corp(
            session, corporation_id=corp.id
        )
    return sales, expenses


async def _confirm(suggestion_id):
    async with SessionLocal() as session:
        return await moves_app.confirm_move(
            session, corporation_eve_id=CORP_ID, suggestion_id=suggestion_id,
            confirmed_by_character_id=CHAR_ID, confirmed_by_name="Boss", now=NOW,
        )


# --- the sync pairs already-sold stock, suggest-only --------------------------------


async def test_estimated_cost_sales_at_any_station_pair():
    esi = MultiHangarEsi()
    await _seed(esi, prices={TRIT: "4.00"})
    await _book_lot(TRIT, 1000, JITA)
    # 60 gone from the Jita hangar; the division's fills at Dodixie booked the
    # no-lot fallback (deemed 90% × 4.00 = 3.60, flagged) — sold evidence.
    assert await _sell(TRIT, 60, DODIXIE, ref=101) is True
    esi.hangar = {(JITA, TRIT): 940}

    await _run(esi)

    _, events, suggestions = await _state()
    shortfall = next(e for e in events if e.kind == "shortfall")
    assert (shortfall.qty, shortfall.flagged) == (60, True)
    (s,) = suggestions
    assert (s.qty, s.qty_listed, s.qty_sold, s.excess_lot_id) == (60, 0, 60, None)
    assert s.destination_location_id == DODIXIE

    # Same world, later sync: the pending pair is never duplicated.
    await _run(esi)
    _, _, suggestions = await _state()
    assert len(suggestions) == 1


async def test_triple_signal_pair_sums_without_double_counting():
    esi = MultiHangarEsi()
    await _seed(esi, prices={TRIT: "4.00"})
    await _book_lot(TRIT, 1000, JITA)
    # At Amarr: 50 already sold via the fallback, 40 counted in the hangar
    # beyond the books, 60 more in sell-order escrow. 150 missing at Jita.
    assert await _sell(TRIT, 50, AMARR, ref=102) is True
    esi.hangar = {(JITA, TRIT): 850, (AMARR, TRIT): 40}
    await _list_orders([(AMARR, TRIT, 60)])

    await _run(esi)

    lots, _, suggestions = await _state()
    deemed = next(lot for lot in lots if lot.location_id == AMARR)
    (s,) = suggestions
    assert (s.qty, s.qty_listed, s.qty_sold) == (150, 60, 50)
    assert s.excess_lot_id == deemed.id  # the counted 40 kept its deemed lot


# --- confirm: retire FIFO + one aggregate true-up, sale rows frozen -----------------


async def test_confirm_retires_fifo_and_books_a_positive_true_up():
    esi = MultiHangarEsi()
    await _seed(esi, prices={TRIT: "4.00"})
    lot = await _book_lot(TRIT, 1000, JITA, unit_cost="5.00")
    await _sell(TRIT, 60, DODIXIE, ref=103)
    esi.hangar = {(JITA, TRIT): 940}
    await _run(esi)
    _, _, (suggestion,) = await _state()

    result = await _confirm(suggestion.id)
    assert result.qty_moved == 60
    # Real landed cost 60 × 5.00 = 300 vs estimated COGS 60 × 3.60 = 216: the
    # sales overstated profit by 84 — a positive cost correction.
    assert result.true_up == Decimal("84.00")

    lots, events, pending = await _state()
    assert pending == []
    # The origin lot was consumed in place — nothing relocated to Dodixie
    # (those units are gone; there is nothing there to hold).
    origin = next(lot_ for lot_ in lots if lot_.id == lot.id)
    assert (origin.qty_remaining, origin.location_id) == (940, JITA)
    assert all(lot_.location_id != DODIXIE for lot_ in lots)
    # ONE aggregate true-up expense, system-booked, attributed to the move.
    sales, expenses = await _sales_and_expenses()
    (true_up,) = [e for e in expenses if e.kind == "cost_true_up"]
    assert true_up.amount == Decimal("84.00")
    assert true_up.source == "system"
    # The sale rows are untouched: still the estimate, still flagged as one.
    assert [(x.qty, x.unit_cost, x.cost_is_estimated) for x in sales] == [
        (60, Decimal("3.60"), True)
    ]
    # Surfaced in plain English: a positive cost correction lowers profit.
    logged = next(e for e in events if e.kind == "move_confirmed")
    assert "profit corrected by -84.00 ISK" in logged.note

    # Total realized profit for the paired quantity is now exact:
    # 60 × 6.10 − 60 × 3.60 (estimated COGS) − 84 (true-up) = 66 = 60 × (6.10 − 5.00).
    async with SessionLocal() as session:
        view = await profit_app.get_profit(session, corporation_eve_id=CORP_ID)
    assert view.profit == Decimal("66.00")

    # And the next sync is clean: the origin's books match the hangar again,
    # and the retired quantity never re-pairs.
    check = await _run(esi)
    assert check.lots_added == 0 and check.flagged == 0
    _, _, suggestions = await _state()
    assert suggestions == []


async def test_negative_difference_books_a_negative_correction():
    esi = MultiHangarEsi()
    await _seed(esi, prices={TRIT: "4.00"})
    # Real cost BELOW the 3.60 estimate: the sales understated profit.
    await _book_lot(TRIT, 1000, JITA, unit_cost="2.00")
    await _sell(TRIT, 60, DODIXIE, ref=104)
    esi.hangar = {(JITA, TRIT): 940}
    await _run(esi)
    _, _, (suggestion,) = await _state()

    result = await _confirm(suggestion.id)
    assert result.true_up == Decimal("-96.00")

    _, expenses = await _sales_and_expenses()
    (true_up,) = [e for e in expenses if e.kind == "cost_true_up"]
    assert true_up.amount == Decimal("-96.00")
    _, events, _ = await _state()
    logged = next(e for e in events if e.kind == "move_confirmed")
    assert "profit corrected by +96.00 ISK" in logged.note


async def test_double_confirm_is_a_no_op_and_retired_qty_never_repairs():
    esi = MultiHangarEsi()
    await _seed(esi, prices={TRIT: "4.00"})
    await _book_lot(TRIT, 1000, JITA)
    await _sell(TRIT, 60, DODIXIE, ref=105)
    esi.hangar = {(JITA, TRIT): 940}
    await _run(esi)
    _, _, (suggestion,) = await _state()

    first = await _confirm(suggestion.id)
    again = await _confirm(suggestion.id)
    assert first.qty_moved == 60
    assert (again.qty_moved, again.true_up) == (0, Decimal(0))

    # One true-up, not two — and the estimated sale rows stay retired for good:
    # further syncs re-pair nothing even though the rows still say estimated.
    _, expenses = await _sales_and_expenses()
    assert len([e for e in expenses if e.kind == "cost_true_up"]) == 1
    await _run(esi)
    await _run(esi)
    _, _, suggestions = await _state()
    assert suggestions == []


async def test_stranded_remainder_repairs_as_sold_and_retires():
    esi = MultiHangarEsi()
    await _seed(esi, prices={TRIT: "4.00"})
    lot = await _book_lot(TRIT, 100, JITA, unit_cost="5.00")
    # The whole 100 was hauled to the Amarr hangar; the sync pairs the counted
    # excess and books the deemed lot (the #200 case).
    esi.hangar = {(JITA, TRIT): 0, (AMARR, TRIT): 100}
    await _run(esi)
    _, _, (counted_pair,) = await _state()
    assert counted_pair.qty == 100 and counted_pair.qty_sold == 0

    # 60 sell out of the deemed lot BEFORE anyone confirms (the ADR's
    # partial-consumption case): the confirm converts only the remainder — 40
    # relocate, and 60 real units strand at Jita with nothing left to pair.
    assert await _sell(TRIT, 60, AMARR, ref=106) is False
    result = await _confirm(counted_pair.id)
    assert result.qty_moved == 40 and result.true_up == Decimal(0)

    # The next sync closes the loop (#207): the stranded 60 re-flag as a
    # shortfall and re-pair against the 60 estimated-cost sales at Amarr.
    esi.hangar = {(JITA, TRIT): 0, (AMARR, TRIT): 40}
    await _run(esi)
    _, _, (sold_pair,) = await _state()
    assert (sold_pair.qty, sold_pair.qty_sold) == (60, 60)
    assert sold_pair.destination_location_id == AMARR

    result = await _confirm(sold_pair.id)
    assert result.qty_moved == 60
    # The 60 retired at their real 5.00 against the 3.60 the sales recognized.
    assert result.true_up == Decimal("84.00")
    lots, _, pending = await _state()
    assert pending == []
    assert all(lot_.id != lot.id or lot_.qty_remaining == 0 for lot_ in lots)
    # Everything now converges: both hangars match the books, nothing pairs.
    check = await _run(esi)
    assert check.lots_added == 0 and check.flagged == 0
    _, _, suggestions = await _state()
    assert suggestions == []
