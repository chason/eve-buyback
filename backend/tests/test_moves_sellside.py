"""#206 / ADR-0049 sell-side pairing, listed stock: the excess end of a "looks
like a move" pair can be the division's unexplained sell-order escrow at ANY
station it trades at (the wallet division scopes the sell side, ADR-0045 — not
the configured-hangar list). Listed quantity explained by idle lots there never
pairs; counted and listed signals are disjoint and sum toward the same cap.
Confirming relocates real lots FIFO exactly like the hangar case — no deemed
lot to reverse — so subsequent fills consume the verified cost, and the
ADR-0044 escrow offset keeps the destination's hangar sync clean."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.application import corp_esi_token as corp_esi_token_app
from app.application import moves as moves_app
from app.application import reconciliation as recon_app
from app.application import sales as sales_app
from app.application.auth import AuthenticatedUser
from app.data.db import SessionLocal
from app.data.models import MarketPrice
from app.data.repositories import buyback_config as config_repo
from app.data.repositories import corporations as corporations_repo
from app.data.repositories import entitlements as entitlements_repo
from app.data.repositories import hangars as hangars_repo
from app.data.repositories import lots as lots_repo
from app.data.repositories import market_orders as orders_repo
from app.data.repositories import move_suggestions as moves_repo
from app.data.repositories import reconciliation as recon_repo
from app.data.repositories import sales as sales_repo
from app.data.repositories import sde as sde_repo
from app.domain.moves import MovePair, match_move_pairs, unexplained_listed
from app.plugins.esi import CharacterInfo, CorporationAsset, CorporationInfo
from app.plugins.sso import OAuthToken, VerifiedCharacter
from app.plugins.token_cipher import get_token_cipher
from tests.helpers import CHAR_ID, CORP_ID

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
JITA = "60003760"
AMARR = "60008494"
DODIXIE = "60011866"  # never a configured hangar in these tests
TRIT = 34


# --- the pure pairing function -----------------------------------------------------


def test_listed_evidence_pairs_without_any_counted_excess():
    pairs = match_move_pairs(
        shortfalls=[(JITA, TRIT, 100)],
        excesses=[],
        listed=[(DODIXIE, TRIT, 60)],
    )
    assert pairs == [
        MovePair(
            type_id=TRIT,
            origin_location_id=JITA,
            destination_location_id=DODIXIE,
            qty=60,
            qty_counted=0,
            qty_listed=60,
        )
    ]


def test_counted_and_listed_sum_toward_the_cap_counted_first():
    # 40 counted + 60 listed at the same station, 70 missing: the pair takes
    # all 40 counted (there's a deemed lot on the books to reverse) and fills
    # the rest from the listed evidence.
    pairs = match_move_pairs(
        shortfalls=[(JITA, TRIT, 70)],
        excesses=[(AMARR, TRIT, 40)],
        listed=[(AMARR, TRIT, 60)],
    )
    (pair,) = pairs
    assert (pair.qty, pair.qty_counted, pair.qty_listed) == (70, 40, 30)


def test_signals_at_different_stations_are_ambiguous_and_pair_nothing():
    # Counted excess at Amarr AND listed evidence at Dodixie: two candidate
    # destinations — the manager-picks flow is #203, so nothing proposes here.
    pairs = match_move_pairs(
        shortfalls=[(JITA, TRIT, 100)],
        excesses=[(AMARR, TRIT, 40)],
        listed=[(DODIXIE, TRIT, 60)],
    )
    assert pairs == []


def test_listed_evidence_at_the_origin_itself_never_pairs():
    assert match_move_pairs(
        shortfalls=[(JITA, TRIT, 100)], excesses=[], listed=[(JITA, TRIT, 60)]
    ) == []


def test_unexplained_listed_floors_at_what_idle_lots_explain():
    listed = {(DODIXIE, TRIT): 100, (AMARR, TRIT): 50}
    idle = {(DODIXIE, TRIT): 40, (AMARR, TRIT): 50}
    # Dodixie: 60 beyond the books; Amarr: fully explained — the normal listed
    # state the ADR-0044 escrow offset already accounts for.
    assert unexplained_listed(listed, idle) == [(DODIXIE, TRIT, 60)]


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
    unit_cost: str = "5.25",
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


async def _list_orders(rows: list[tuple[str, int, int]]) -> None:
    """Snapshot the division's sell orders as the sales ingestion would
    (`(location_id, type_id, volume_remain)` each) — replace-style."""
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


async def _confirm(suggestion_id):
    async with SessionLocal() as session:
        return await moves_app.confirm_move(
            session, corporation_eve_id=CORP_ID, suggestion_id=suggestion_id,
            confirmed_by_character_id=CHAR_ID, confirmed_by_name="Boss", now=NOW,
        )


# --- the sync pairs listed stock (any station), suggest-only ------------------------


async def test_unexplained_listed_stock_at_any_station_pairs():
    esi = MultiHangarEsi()
    await _seed(esi)
    await _book_lot(TRIT, 1000, JITA)
    # 60 gone from the Jita hangar; the division has 60 listed at Dodixie —
    # not a configured hangar — with no idle lots there to explain them.
    esi.hangar = {(JITA, TRIT): 940}
    await _list_orders([(DODIXIE, TRIT, 60)])

    await _run(esi)

    lots, events, suggestions = await _state()
    # The default treatment stands: the shortfall is flagged; no lot exists or
    # is invented for the listed evidence (nothing was counted anywhere).
    shortfall = next(e for e in events if e.kind == "shortfall")
    assert (shortfall.qty, shortfall.flagged) == (60, True)
    assert all(lot.location_id != DODIXIE for lot in lots)
    # The decoration: a pure sell-side pair — listed portion only, no lot.
    (s,) = suggestions
    assert (s.qty, s.qty_listed, s.excess_lot_id) == (60, 60, None)
    assert s.origin_location_id == JITA
    assert s.destination_location_id == DODIXIE
    assert s.shortfall_event_id == shortfall.id

    # A later sync seeing the same world must not duplicate the pending pair.
    await _run(esi)
    _, _, suggestions = await _state()
    assert len(suggestions) == 1


async def test_listed_stock_explained_by_idle_lots_there_does_not_pair():
    esi = MultiHangarEsi()
    await _seed(esi)
    await _book_lot(TRIT, 1000, JITA)
    # The books DO have 40 idle at Dodixie: of the 100 listed, only the 60
    # beyond those lots are evidence that stock arrived off the books.
    await _book_lot(TRIT, 40, DODIXIE)
    esi.hangar = {(JITA, TRIT): 900}
    await _list_orders([(DODIXIE, TRIT, 100)])

    await _run(esi)

    _, _, suggestions = await _state()
    (s,) = suggestions
    assert (s.qty, s.qty_listed) == (60, 60)


async def test_fully_explained_listed_stock_pairs_nothing():
    esi = MultiHangarEsi()
    await _seed(esi)
    await _book_lot(TRIT, 1000, JITA)
    await _book_lot(TRIT, 100, DODIXIE)
    esi.hangar = {(JITA, TRIT): 940}
    await _list_orders([(DODIXIE, TRIT, 100)])

    await _run(esi)

    _, events, suggestions = await _state()
    assert suggestions == []  # the shortfall stays a plain flagged shortfall
    assert next(e for e in events if e.kind == "shortfall").flagged is True


async def test_counted_and_listed_signals_sum_without_double_counting():
    esi = MultiHangarEsi()
    await _seed(esi, prices={TRIT: "4.00"})
    await _book_lot(TRIT, 1000, JITA)
    # 100 missing at Jita; at the Amarr hangar 40 were COUNTED beyond the books
    # (→ deemed lot) and 60 more sit in sell-order escrow (counts can't see
    # escrow — disjoint by construction). One pair, both portions.
    esi.hangar = {(JITA, TRIT): 900, (AMARR, TRIT): 40}
    await _list_orders([(AMARR, TRIT, 60)])

    await _run(esi)

    lots, _, suggestions = await _state()
    deemed = next(lot for lot in lots if lot.location_id == AMARR)
    assert deemed.qty_remaining == 40 and deemed.cost_is_estimated is True
    (s,) = suggestions
    assert (s.qty, s.qty_listed, s.excess_lot_id) == (100, 60, deemed.id)


# --- #202 interplay: a listing that stops pairing withdraws the suggestion ----------


async def test_sold_out_or_cancelled_listing_withdraws_the_pending_pair():
    esi = MultiHangarEsi()
    await _seed(esi)
    await _book_lot(TRIT, 1000, JITA)
    esi.hangar = {(JITA, TRIT): 940}
    await _list_orders([(DODIXIE, TRIT, 60)])
    await _run(esi)
    _, _, (pending,) = await _state()
    assert pending.qty_listed == 60

    # The listing disappears (cancelled, or fully sold — the sold signal is
    # #207) before anyone confirms: the evidence is gone, so the next sync
    # takes the suggestion back. A withdrawal, not a decision — logged as such,
    # and it never suppresses a future pairing the way a dismissal does.
    await _list_orders([])
    await _run(esi)

    _, events, suggestions = await _state()
    assert suggestions == []
    withdrawn = next(e for e in events if e.kind == "move_withdrawn")
    assert (withdrawn.location_id, withdrawn.qty) == (JITA, 60)


async def test_partly_sold_listing_withdraws_and_repairs_at_the_smaller_qty():
    esi = MultiHangarEsi()
    await _seed(esi)
    await _book_lot(TRIT, 1000, JITA)
    esi.hangar = {(JITA, TRIT): 940}
    await _list_orders([(DODIXIE, TRIT, 60)])
    await _run(esi)

    # 35 of the 60 sell: the old pair's listed portion is no longer backed, so
    # it's withdrawn — and the SAME pass re-proposes from the evidence that
    # still holds (the shortfall flag still stands, so the smaller pair is
    # anchored to the same event).
    await _list_orders([(DODIXIE, TRIT, 25)])
    await _run(esi)

    _, events, suggestions = await _state()
    assert any(e.kind == "move_withdrawn" for e in events)
    (s,) = suggestions
    assert (s.qty, s.qty_listed) == (25, 25)


# --- confirm: FIFO relocation, verified cost for subsequent fills -------------------


async def test_confirm_relocates_and_subsequent_fills_consume_verified_cost():
    esi = MultiHangarEsi()
    await _seed(esi)
    old = await _book_lot(TRIT, 50, JITA, unit_cost="5.00",
                          acquired_at=NOW - timedelta(days=40))
    newer = await _book_lot(TRIT, 950, JITA, unit_cost="6.00",
                            acquired_at=NOW - timedelta(days=10))
    esi.hangar = {(JITA, TRIT): 940}
    await _list_orders([(DODIXIE, TRIT, 60)])
    await _run(esi)
    _, _, (suggestion,) = await _state()

    result = await _confirm(suggestion.id)
    assert result.qty_moved == 60

    # FIFO: the 40-day-old lot moved whole (in place, aging intact); the newer
    # one split for the remaining 10 — cost fields carried unchanged.
    lots, events, pending = await _state()
    assert pending == []
    at_dodixie = [lot for lot in lots if lot.location_id == DODIXIE]
    moved_old = next(lot for lot in at_dodixie if lot.id == old.id)
    assert moved_old.qty_remaining == 50
    child = next(lot for lot in at_dodixie if lot.id != old.id)
    assert (child.qty_remaining, child.source_lot_id) == (10, newer.id)
    assert child.unit_purchase_cost == Decimal("6.00")
    assert all(lot.cost_is_estimated is False for lot in at_dodixie)
    logged = next(e for e in events if e.kind == "move_confirmed")
    assert (logged.location_id, logged.qty) == (JITA, 60)
    assert "Dodixie IX" in logged.note

    # The point of confirming early: the fills that come after consume the
    # relocated lots at their verified cost — no no-lot fallback, no flag.
    async with SessionLocal() as session:
        corp = await corporations_repo.get_by_eve_id(session, CORP_ID)
        unmatched = await sales_app.consume_and_record_sale(
            session, corp.id, type_id=TRIT, qty=60,
            unit_proceeds=Decimal("6.10"), location_id=DODIXIE,
            channel="market", external_ref=777, sold_at=NOW, now=NOW,
        )
        await session.commit()
    assert unmatched is False
    async with SessionLocal() as session:
        corp = await corporations_repo.get_by_eve_id(session, CORP_ID)
        sales = await sales_repo.list_for_corp(session, corporation_id=corp.id)
    assert sorted((s.qty, s.unit_cost) for s in sales) == [
        (10, Decimal("6.00")), (50, Decimal("5.00")),
    ]
    assert all(s.cost_is_estimated is False for s in sales)

    # And the next hangar sync is clean at BOTH ends: the origin's books now
    # match its hangar, and the destination isn't a hangar at all — the sold
    # units left the ledger through the sale, not a discrepancy.
    esi.hangar = {(JITA, TRIT): 940}
    await _list_orders([])
    result = await _run(esi)
    assert result.lots_added == 0 and result.flagged == 0
    _, _, suggestions = await _state()
    assert suggestions == []


async def test_confirm_mixed_pair_reverses_only_the_counted_portion():
    esi = MultiHangarEsi()
    await _seed(esi, prices={TRIT: "4.00"})
    await _book_lot(TRIT, 1000, JITA, unit_cost="5.25")
    esi.hangar = {(JITA, TRIT): 900, (AMARR, TRIT): 40}
    await _list_orders([(AMARR, TRIT, 60)])
    await _run(esi)
    _, _, (suggestion,) = await _state()
    deemed_id = suggestion.excess_lot_id

    result = await _confirm(suggestion.id)
    assert result.qty_moved == 100

    lots, _, _ = await _state()
    # The deemed lot (counted portion, 40) is reversed entirely; the listed
    # portion had no lot to reverse. 100 relocated at the verified cost.
    assert all(lot.id != deemed_id for lot in lots)
    at_amarr = [lot for lot in lots if lot.location_id == AMARR]
    assert sum(lot.qty_remaining for lot in at_amarr) == 100
    assert all(lot.unit_purchase_cost == Decimal("5.25") for lot in at_amarr)
    assert all(lot.cost_is_estimated is False for lot in at_amarr)


# --- the card ----------------------------------------------------------------------


async def test_sellside_card_resolves_station_names_and_carries_the_portion():
    esi = MultiHangarEsi()
    await _seed(esi)
    await _book_lot(TRIT, 1000, JITA)
    esi.hangar = {(JITA, TRIT): 940}
    await _list_orders([(DODIXIE, TRIT, 60)])
    await _run(esi)

    async with SessionLocal() as session:
        views = await recon_app.list_move_suggestions(
            session, corporation_eve_id=CORP_ID
        )
    (view,) = views
    assert view.record.qty_listed == 60
    assert view.origin_name == "Jita IV - Moon 4"
    # Not a configured hangar → the seeded SDE station name.
    assert view.destination_name == (
        "Dodixie IX - Moon 20 - Federation Navy Assembly Plant"
    )
