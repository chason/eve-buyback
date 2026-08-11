"""#156 / ADR-0045: market-sales ingestion — FIFO-consuming fills with
transaction-id idempotency, no-lot sales at deemed COGS (flagged), journal tax
attached to fills (SET, not add), broker fees as expenses, the order snapshot with
the wrong-division guard and the listed-state coupling into the hangar
reconciliation, and the wallet-division config API."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.application import corp_esi_token as corp_esi_token_app
from app.application import sales as sales_app
from app.application.auth import AuthenticatedUser
from app.data.db import SessionLocal
from app.data.models import MarketPrice
from app.data.repositories import buyback_config as config_repo
from app.data.repositories import corporations as corporations_repo
from app.data.repositories import entitlements as entitlements_repo
from app.data.repositories import expenses as expenses_repo
from app.data.repositories import lots as lots_repo
from app.data.repositories import reconciliation as recon_repo
from app.data.repositories import sales as sales_repo
from app.data.repositories import sde as sde_repo
from app.main import app
from app.plugins.esi import (
    CharacterInfo,
    CorpJournalEntry,
    CorpMarketOrder,
    CorporationInfo,
    CorpWalletTransaction,
)
from app.plugins.sso import OAuthToken, VerifiedCharacter
from app.plugins.token_cipher import get_token_cipher
from tests.helpers import CHAR_ID, CORP_ID, CeoEsi, login, make_client, office_nested_assets

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
JITA = "60003760"
TRIT = 34
DIVISION = 3


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


class FakeSso:
    configured = True

    def build_authorize_url(self, *, state, code_challenge, scopes=None):
        return "https://login.eveonline.com/authorize"

    async def exchange_code(self, code, code_verifier):
        return OAuthToken(access_token="a", refresh_token="r")

    async def verify_token(self, access_token):
        return VerifiedCharacter(character_id=CHAR_ID, name="Boss")

    async def refresh_access_token(self, refresh_token):
        return OAuthToken(access_token="fresh", refresh_token=refresh_token)


class SalesEsi:
    """ESI fake: token-connect validation + the three sell-side reads."""

    def __init__(self):
        self.transactions: list[CorpWalletTransaction] = []
        self.journal: list[CorpJournalEntry] = []
        self.orders: list[CorpMarketOrder] = []
        self.calls: list[str] = []

    async def get_character(self, character_id):
        return CharacterInfo(name="Boss", corporation_id=CORP_ID)

    async def get_character_corporation(self, character_id):
        return CORP_ID

    async def get_corporation(self, corporation_id):
        return CorporationInfo(name="Test Corp", ceo_id=CHAR_ID, ticker="T")

    async def get_character_roles(self, character_id, access_token):
        return []

    async def get_corporation_wallet_transactions(self, corp, division, token):
        self.calls.append(f"transactions:{division}")
        return list(self.transactions)

    async def get_corporation_wallet_journal(self, corp, division, token):
        self.calls.append(f"journal:{division}")
        return list(self.journal)

    async def get_corporation_orders(self, corp, token):
        self.calls.append("orders")
        return list(self.orders)


def _tx(txid, *, qty, unit_price="5.00", when=NOW, type_id=TRIT, is_buy=False):
    return CorpWalletTransaction(
        transaction_id=txid, date=when, type_id=type_id, quantity=qty,
        unit_price=Decimal(unit_price), is_buy=is_buy, location_id=int(JITA),
    )


def _order(order_id, *, remain, division=DIVISION, type_id=TRIT, is_buy=False):
    return CorpMarketOrder(
        order_id=order_id, type_id=type_id, is_buy_order=is_buy,
        price=Decimal("6.00"), volume_remain=remain, volume_total=remain,
        location_id=int(JITA), wallet_division=division, issued=NOW,
    )


def _user() -> AuthenticatedUser:
    return AuthenticatedUser(
        character_id=CHAR_ID, character_name="Boss", corporation_id=CORP_ID,
        corporation_name="Test Corp", role="ceo", is_director=False,
        corporation_registered=True,
    )


async def _seed(esi, *, division=DIVISION, prices=None):
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
        if division is not None:
            await config_repo.set_wallet_division(
                session, corporation_id=corp.id, division=division
            )
        await sde_repo.bulk_upsert_types(session, [
            {"type_id": TRIT, "name": "Tritanium", "group_id": 18,
             "market_group_id": 1857, "volume": 0.01, "portion_size": 1,
             "published": True},
        ])
        for type_id, buy in (prices or {}).items():
            b = Decimal(buy)
            session.add(MarketPrice(
                hub_id=JITA, type_id=type_id,
                buy_weighted_average=b, buy_max=b, buy_min=b, buy_median=b,
                buy_percentile=b, buy_volume=Decimal(1000), buy_order_count=10,
                sell_weighted_average=b, sell_max=b, sell_min=b, sell_median=b,
                sell_percentile=b, sell_volume=Decimal(1000), sell_order_count=10,
                fetched_at=NOW,
            ))
        await session.commit()
        corp_id = corp.id
    async with SessionLocal() as session:
        await corp_esi_token_app.complete_corp_esi_authorize(
            session, FakeSso(), esi, code="c", verifier="v",
            user=_user(), cipher=get_token_cipher(),
        )
    return corp_id


async def _lot(corp_id, *, qty, cost, days_ago=10, type_id=TRIT):
    async with SessionLocal() as session:
        lot = await lots_repo.create_lot(
            session, corporation_id=corp_id, item_type_id=type_id, qty=qty,
            unit_purchase_cost=Decimal(cost),
            acquired_at=NOW - timedelta(days=days_ago),
            source="buyback", location_id=JITA,
        )
        await session.commit()
        return lot


async def _run(esi):
    async with SessionLocal() as session:
        return await sales_app.ingest_market_sales(
            session, FakeSso(), esi, corporation_eve_id=CORP_ID,
            cipher=get_token_cipher(), now=NOW,
        )


# --- fills → FIFO sales ---------------------------------------------------------


async def test_fill_consumes_lots_fifo_and_is_idempotent():
    esi = SalesEsi()
    corp_id = await _seed(esi)
    old = await _lot(corp_id, qty=100, cost="3.60", days_ago=30)
    new = await _lot(corp_id, qty=100, cost="3.80", days_ago=1)
    esi.transactions = [_tx(1001, qty=150, unit_price="5.00")]

    result = await _run(esi)
    assert result.sales_recorded == 1 and result.flagged == 0

    async with SessionLocal() as session:
        rows = await sales_repo.list_for_corp(session, corporation_id=corp_id)
        lots = await lots_repo.open_lots(session, corporation_id=corp_id)
    # One sale row per lot touched: 100 from the old lot, 50 from the new (FIFO).
    assert {(r.lot_id, r.qty) for r in rows} == {(old.id, 100), (new.id, 50)}
    assert all(
        r.unit_proceeds == Decimal("5.00")
        and r.external_ref == 1001
        and r.channel == "market"
        and r.source == "esi"
        for r in rows
    )
    assert [(lot.id, lot.qty_remaining) for lot in lots] == [(new.id, 50)]

    # Re-polling the same page records nothing new.
    again = await _run(esi)
    assert again.sales_recorded == 0
    async with SessionLocal() as session:
        rows = await sales_repo.list_for_corp(session, corporation_id=corp_id)
    assert len(rows) == 2


async def test_buy_transactions_are_ignored():
    esi = SalesEsi()
    corp_id = await _seed(esi)
    esi.transactions = [_tx(1002, qty=10, is_buy=True)]
    result = await _run(esi)
    assert result.sales_recorded == 0
    async with SessionLocal() as session:
        assert await sales_repo.list_for_corp(session, corporation_id=corp_id) == []


async def test_no_lot_sale_books_deemed_cogs_and_flags():
    esi = SalesEsi()
    corp_id = await _seed(esi, prices={TRIT: "4.00"})
    await _lot(corp_id, qty=100, cost="3.60")
    esi.transactions = [_tx(1003, qty=150, unit_price="5.00")]

    result = await _run(esi)
    assert result.sales_recorded == 1 and result.flagged == 1

    async with SessionLocal() as session:
        rows = await sales_repo.list_for_corp(session, corporation_id=corp_id)
        events = await recon_repo.list_for_corp(session, corporation_id=corp_id)
    # The 50 the books didn't have: a deemed-cost estimated lot, consumed at once.
    assert sorted(r.qty for r in rows) == [50, 100]
    (event,) = events
    assert event.kind == "unmatched_sale"
    assert (event.qty, event.flagged) == (50, True)
    assert event.unit_cost == Decimal("3.60")  # 90% × Jita buy 4.00 (deemed policy)
    async with SessionLocal() as session:
        deemed_lot = await lots_repo.get_for_corp(
            session, corporation_id=corp_id, lot_id=event.lot_id
        )
    assert deemed_lot.cost_is_estimated is True
    assert deemed_lot.qty_remaining == 0  # created for the sale, fully consumed


async def test_no_lot_sale_without_any_price_deems_at_proceeds():
    esi = SalesEsi()
    corp_id = await _seed(esi)  # no cached prices at all
    esi.transactions = [_tx(1004, qty=10, unit_price="7.00")]

    await _run(esi)

    async with SessionLocal() as session:
        events = await recon_repo.list_for_corp(session, corporation_id=corp_id)
    # Proceeds are the only cost signal left → zero estimated profit, never a gain.
    assert events[0].unit_cost == Decimal("7.00")


# --- journal → tax + fees --------------------------------------------------------


async def test_tax_attaches_to_the_fill_and_fees_book_once():
    esi = SalesEsi()
    corp_id = await _seed(esi)
    await _lot(corp_id, qty=200, cost="3.60")
    esi.transactions = [_tx(1005, qty=150, unit_price="5.00")]
    esi.journal = [
        CorpJournalEntry(id=50_001, ref_type="transaction_tax",
                         amount=Decimal("-27.00"), date=NOW, context_id=1005),
        CorpJournalEntry(id=50_002, ref_type="brokers_fee",
                         amount=Decimal("-11.25"), date=NOW),
        CorpJournalEntry(id=50_003, ref_type="player_donation",
                         amount=Decimal("1000000"), date=NOW),
    ]

    result = await _run(esi)
    assert result.fees_booked == 1

    async with SessionLocal() as session:
        rows = await sales_repo.list_for_corp(session, corporation_id=corp_id)
        expense_rows = await expenses_repo.list_for_corp(
            session, corporation_id=corp_id
        )
    assert [r.sales_tax for r in rows] == [Decimal("27.00")]
    (fee,) = expense_rows
    assert (fee.kind, fee.amount, fee.source, fee.external_ref) == (
        "broker_fee", Decimal("11.25"), "esi", 50_002,
    )

    # Re-poll: tax SET again (same value, no double count); the fee not re-booked.
    again = await _run(esi)
    assert again.fees_booked == 0
    async with SessionLocal() as session:
        rows = await sales_repo.list_for_corp(session, corporation_id=corp_id)
        expense_rows = await expenses_repo.list_for_corp(
            session, corporation_id=corp_id
        )
    assert [r.sales_tax for r in rows] == [Decimal("27.00")]
    assert len(expense_rows) == 1


# --- orders → listed snapshot + division guard ------------------------------------


async def test_off_division_sell_order_is_flagged_once():
    esi = SalesEsi()
    corp_id = await _seed(esi)
    esi.orders = [
        _order(9001, remain=500),  # right division — fine
        _order(9002, remain=200, division=1),  # pays into the wrong wallet
    ]

    result = await _run(esi)
    assert result.flagged == 1
    async with SessionLocal() as session:
        events = await recon_repo.list_for_corp(session, corporation_id=corp_id)
    (event,) = events
    assert event.kind == "unexpected_division"
    assert (event.qty, event.flagged) == (200, True)

    # Unchanged on the next pass → not re-logged.
    again = await _run(esi)
    assert again.flagged == 0


async def test_listed_units_reduce_the_hangar_expectation():
    """The coupling into ADR-0044: sell-order escrow has left the hangar, so a
    hangar holding (stock − listed) reconciles clean."""
    from app.application import reconciliation as recon_app
    from app.data.repositories import buyback_locations as locations_repo
    from app.data.repositories import hangars as hangars_repo

    esi = SalesEsi()
    corp_id = await _seed(esi)
    await _lot(corp_id, qty=1000, cost="3.60")
    esi.orders = [_order(9003, remain=300)]
    await _run(esi)  # snapshot the orders

    async with SessionLocal() as session:
        await locations_repo.add(
            session, corp_id, kind="npc_station", location_id=JITA,
            name="Jita IV - Moon 4", system_name="Jita",
        )
        await hangars_repo.add(
            session, corporation_id=corp_id, location_id=JITA,
            location_name="Jita IV - Moon 4", division=2,
        )
        await session.commit()

    class HangarEsi(SalesEsi):
        async def get_corporation_assets(self, corp, token):
            return office_nested_assets({(JITA, TRIT): 700}, iter([1]))

    hangar_esi = HangarEsi()
    async with SessionLocal() as session:
        result = await recon_app.reconcile_hangars(
            session, FakeSso(), hangar_esi, corporation_eve_id=CORP_ID,
            cipher=get_token_cipher(), excess_flag_isk=10**12, now=NOW,
        )
    # 1000 on the books − 300 listed = 700 expected; the hangar holds 700 → clean.
    assert result.lots_added == 0 and result.flagged == 0


async def test_unconfigured_division_is_a_no_op():
    esi = SalesEsi()
    await _seed(esi, division=None)
    esi.transactions = [_tx(1006, qty=10)]
    result = await _run(esi)
    assert result == sales_app.SalesIngestResult(
        sales_recorded=0, fees_booked=0, flagged=0
    )
    assert esi.calls == []  # not even an orders read


# --- wallet-division API -----------------------------------------------------------


async def test_wallet_division_api_round_trip():
    esi = SalesEsi()
    await _seed(esi, division=None)
    app.dependency_overrides.clear()
    async with make_client(CeoEsi()) as http:
        await login(http)
        resp = await http.get("/api/v1/corporations/me/accounting/wallet-division")
        assert resp.json() == {"division": None}

        resp = await http.put(
            "/api/v1/corporations/me/accounting/wallet-division",
            json={"division": 3},
        )
        assert resp.json() == {"division": 3}

        resp = await http.put(
            "/api/v1/corporations/me/accounting/wallet-division",
            json={"division": None},
        )
        assert resp.json() == {"division": None}

        resp = await http.put(
            "/api/v1/corporations/me/accounting/wallet-division",
            json={"division": 8},
        )
        assert resp.status_code == 422
