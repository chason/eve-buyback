"""#159: the "How we're doing" profit view — the pure domain fold, the sale-time
COGS snapshot (write-downs never rewrite past sales), period bounds, sale-event
counting across multi-lot fills, the measured/estimated split, and the API."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.application import profit as profit_app
from app.application import sales as sales_app
from app.application.errors import EntitlementRequired
from app.data.db import SessionLocal
from app.data.repositories import corporations as corporations_repo
from app.data.repositories import entitlements as entitlements_repo
from app.data.repositories import expenses as expenses_repo
from app.data.repositories import lots as lots_repo
from app.domain.profit import SaleFact, summarize_sales
from tests.helpers import CHAR_ID, CORP_ID, CeoEsi, login, make_client

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
JITA = "60003760"
TRIT = 34


# --- domain fold ------------------------------------------------------------------


def _fact(**kw):
    defaults = dict(
        channel="market", qty=10, unit_proceeds=Decimal("5"),
        unit_cost=Decimal("4"), sales_tax=Decimal(0), cost_is_estimated=False,
    )
    defaults.update(kw)
    return SaleFact(**defaults)


def test_summarize_folds_channels_and_keeps_estimates_apart():
    summary = summarize_sales([
        _fact(qty=100, sales_tax=Decimal("20")),                # margin 80
        _fact(channel="contract", qty=10, unit_proceeds=Decimal("6")),  # margin 20
        _fact(channel="direct", qty=5, cost_is_estimated=True),  # margin 5, estimated
    ])
    assert summary.revenue == Decimal("585")
    assert summary.sales_tax == Decimal("20")
    assert summary.cost_of_goods == Decimal("460")
    assert summary.margin == Decimal("105")
    assert summary.measured_margin == Decimal("100")
    assert summary.estimated_margin == Decimal("5")
    by = {c.channel: c for c in summary.by_channel}
    assert set(by) == {"market", "contract", "direct"}  # stable, zeroes included
    assert by["market"].margin == Decimal("80")
    assert by["contract"].margin == Decimal("20")


def test_summarize_empty_is_all_zero():
    summary = summarize_sales([])
    assert summary.margin == Decimal(0)
    assert [c.revenue for c in summary.by_channel] == [Decimal(0)] * 3


# --- application view -------------------------------------------------------------


async def _seed_corp():
    async with SessionLocal() as session:
        corp = await corporations_repo.create_corporation(
            session, eve_corporation_id=CORP_ID, name="Test Corp",
            ceo_character_id=CHAR_ID, registered_by_character_id=CHAR_ID,
        )
        await entitlements_repo.upsert(
            session, corporation_id=corp.id, feature="accounting",
            source="admin", expires_at=None,
        )
        await session.commit()
        return corp.id


async def _lot(corp_id, *, qty, cost, days_ago=10, estimated=False):
    async with SessionLocal() as session:
        lot = await lots_repo.create_lot(
            session, corporation_id=corp_id, item_type_id=TRIT, qty=qty,
            unit_purchase_cost=Decimal(cost),
            acquired_at=NOW - timedelta(days=days_ago),
            source="buyback", location_id=JITA, cost_is_estimated=estimated,
        )
        await session.commit()
        return lot


async def _sell(corp_id, *, qty, price, channel="market", ref=None, sold_at=NOW):
    async with SessionLocal() as session:
        await sales_app.consume_and_record_sale(
            session, corp_id, type_id=TRIT, qty=qty,
            unit_proceeds=Decimal(price), location_id=JITA, channel=channel,
            external_ref=ref, sold_at=sold_at, now=NOW,
        )
        await session.commit()


async def _view(**kw):
    async with SessionLocal() as session:
        return await profit_app.get_profit(
            session, corporation_eve_id=CORP_ID, **kw
        )


async def test_profit_folds_sales_and_expenses():
    corp_id = await _seed_corp()
    await _lot(corp_id, qty=200, cost="4.00")
    await _sell(corp_id, qty=100, price="5.00", ref=1001)
    await _sell(corp_id, qty=50, price="5.00", channel="contract", ref=2001)
    async with SessionLocal() as session:
        await expenses_repo.create_expense(
            session, corporation_id=corp_id, kind="broker_fee",
            amount=Decimal("30"), source="esi", incurred_at=NOW,
        )
        await expenses_repo.create_expense(
            session, corporation_id=corp_id, kind="hauling",
            amount=Decimal("10"), source="manual", incurred_at=NOW,
        )
        await session.commit()

    view = await _view()
    assert view.revenue == Decimal("750")
    assert view.cost_of_goods == Decimal("600")
    assert view.margin == Decimal("150")
    assert view.fees == Decimal("30")
    assert view.other_expenses == Decimal("10")
    assert view.profit == Decimal("110")
    assert view.sale_count == 2
    by = {c.channel: c for c in view.channels}
    assert by["market"].margin == Decimal("100")
    assert by["contract"].margin == Decimal("50")
    assert by["direct"].revenue == Decimal(0)


async def test_multi_lot_fill_counts_as_one_sale():
    corp_id = await _seed_corp()
    await _lot(corp_id, qty=30, cost="4.00", days_ago=20)
    await _lot(corp_id, qty=100, cost="4.20", days_ago=1)
    await _sell(corp_id, qty=50, price="5.00", ref=1002)  # splits across both lots

    view = await _view()
    by = {c.channel: c for c in view.channels}
    assert by["market"].sale_count == 1
    assert view.sale_count == 1


async def test_write_down_after_sale_does_not_rewrite_past_profit():
    """The #159 snapshot rule: COGS is frozen at sale time. The later write-down
    books its own loss; the sold units' cost stays what it was."""
    corp_id = await _seed_corp()
    lot = await _lot(corp_id, qty=100, cost="5.00")
    await _sell(corp_id, qty=40, price="6.00", ref=1003)

    async with SessionLocal() as session:
        await lots_repo.write_down(session, lot_id=lot.id, value=Decimal("4.00"))
        await expenses_repo.create_expense(
            session, corporation_id=corp_id, kind="write_down",
            amount=Decimal("60"),  # (5 − 4) × the 60 units still held
            source="system", incurred_at=NOW, lot_id=lot.id,
        )
        await session.commit()

    view = await _view()
    assert view.cost_of_goods == Decimal("200")  # 40 × 5.00, NOT 40 × 4.00
    assert view.margin == Decimal("40")
    assert view.write_downs == Decimal("60")
    assert view.profit == Decimal("-20")

    # Units sold AFTER the write-down carry the floored cost.
    await _sell(corp_id, qty=60, price="6.00", ref=1004)
    view = await _view()
    assert view.cost_of_goods == Decimal("440")  # 200 + 60 × 4.00
    assert view.margin == Decimal("160")


async def test_estimated_cost_margin_stays_apart():
    corp_id = await _seed_corp()
    await _lot(corp_id, qty=50, cost="4.00", days_ago=20, estimated=True)
    await _lot(corp_id, qty=50, cost="4.00", days_ago=1)
    await _sell(corp_id, qty=100, price="5.00", ref=1005)

    view = await _view()
    assert view.margin == Decimal("100")
    assert view.estimated_margin == Decimal("50")
    assert view.measured_margin == Decimal("50")


async def test_period_bounds_filter_sales_and_expenses():
    corp_id = await _seed_corp()
    await _lot(corp_id, qty=300, cost="4.00", days_ago=60)
    await _sell(corp_id, qty=100, price="5.00", ref=1006,
                sold_at=NOW - timedelta(days=45))
    await _sell(corp_id, qty=100, price="5.00", ref=1007,
                sold_at=NOW - timedelta(days=5))
    async with SessionLocal() as session:
        await expenses_repo.create_expense(
            session, corporation_id=corp_id, kind="broker_fee",
            amount=Decimal("7"), source="esi",
            incurred_at=NOW - timedelta(days=45),
        )
        await session.commit()

    month = await _view(since=NOW - timedelta(days=30), until=NOW)
    assert month.sale_count == 1
    assert month.revenue == Decimal("500")
    assert month.fees == Decimal(0)  # the old fee is outside the window

    all_time = await _view()
    assert all_time.sale_count == 2
    assert all_time.fees == Decimal("7")


async def test_profit_requires_the_entitlement():
    async with SessionLocal() as session:
        await corporations_repo.create_corporation(
            session, eve_corporation_id=CORP_ID, name="Test Corp",
            ceo_character_id=CHAR_ID, registered_by_character_id=CHAR_ID,
        )
        await session.commit()
    with pytest.raises(EntitlementRequired):
        await _view()


# --- API --------------------------------------------------------------------------


async def test_profit_endpoint_serves_the_view():
    corp_id = await _seed_corp()
    await _lot(corp_id, qty=100, cost="4.00")
    await _sell(corp_id, qty=100, price="5.00", ref=1008)

    async with make_client(CeoEsi()) as http:
        await login(http)
        resp = await http.get("/api/v1/corporations/me/accounting/profit")
        assert resp.status_code == 200
        body = resp.json()
        assert Decimal(body["profit"]) == Decimal("100")
        assert body["sale_count"] == 1
        assert {c["channel"] for c in body["channels"]} == {
            "market", "contract", "direct",
        }

        resp = await http.get(
            "/api/v1/corporations/me/accounting/profit",
            params={"since": "2026-09-01T00:00:00Z"},
        )
        assert resp.status_code == 200
        assert Decimal(resp.json()["revenue"]) == Decimal(0)
