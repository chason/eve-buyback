"""#152 / ADR-0043: the "What we've got" inventory view — cost rollups per item,
verified/estimated split, per-lot aging + stale flag, entitlement gate (402), and
the manager gate (403)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import update

from app.application import lots as lots_app
from app.data.db import SessionLocal
from app.data.models import Lot, MarketPrice
from app.data.repositories import buyback_config as config_repo
from app.data.repositories import corporations as corporations_repo
from app.data.repositories import entitlements as entitlements_repo
from app.data.repositories import lots as lots_repo
from app.data.repositories import sde as sde_repo
from app.main import app
from tests.helpers import CHAR_ID, CORP_ID, CeoEsi, MemberEsi, login, make_client

NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)
JITA = "60003760"


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


async def _seed_corp(*, entitled: bool = True):
    async with SessionLocal() as session:
        corp = await corporations_repo.create_corporation(
            session,
            eve_corporation_id=CORP_ID,
            name="Test Corp",
            ceo_character_id=CHAR_ID,
            registered_by_character_id=CHAR_ID,
        )
        if entitled:
            await entitlements_repo.upsert(
                session,
                corporation_id=corp.id,
                feature="accounting",
                source="admin",
                expires_at=None,
            )
        await sde_repo.bulk_upsert_types(session, [
            {"type_id": 34, "name": "Tritanium", "group_id": 18,
             "market_group_id": 1, "volume": 0.01, "published": True},
            {"type_id": 35, "name": "Pyerite", "group_id": 18,
             "market_group_id": 1, "volume": 0.01, "published": True},
        ])
        await session.commit()
        return corp.id


async def _lot(
    corp_id, *, type_id=34, qty=100, cost="4.00", days_ago=0, acquired_at=None,
    **kwargs,
):
    async with SessionLocal() as session:
        lot = await lots_repo.create_lot(
            session,
            corporation_id=corp_id,
            item_type_id=type_id,
            qty=qty,
            unit_purchase_cost=Decimal(cost),
            acquired_at=acquired_at or NOW - timedelta(days=days_ago),
            source=kwargs.pop("source", "buyback"),
            location_id=JITA,
            **kwargs,
        )
        await session.commit()
        return lot.id


# --- application rollup -----------------------------------------------------------


async def test_inventory_rolls_up_per_type_with_verified_estimated_split():
    corp_id = await _seed_corp()
    # Tritanium: an old verified lot + a fresh estimated one. Pyerite: one cheap lot.
    await _lot(corp_id, type_id=34, qty=100, cost="4.00", days_ago=45)
    await _lot(corp_id, type_id=34, qty=50, cost="5.00", days_ago=2,
               source="opening_balance", cost_is_estimated=True)
    await _lot(corp_id, type_id=35, qty=10, cost="1.50", days_ago=1)

    async with SessionLocal() as session:
        view = await lots_app.get_inventory(
            session, corporation_eve_id=CORP_ID, stale_days=30,
            sales_tax_rate=Decimal(0), now=NOW
        )

    assert view.total_cost == Decimal("665.00")  # 400 + 250 + 15
    assert view.estimated_cost == Decimal("250.00")
    assert view.verified_cost == Decimal("415.00")
    assert view.stale_days == 30

    # Sorted by what we paid, biggest first.
    assert [(i.type_id, i.type_name) for i in view.items] == [
        (34, "Tritanium"), (35, "Pyerite"),
    ]
    trit, pye = view.items
    assert (trit.qty, trit.total_cost) == (150, Decimal("650.00"))
    assert trit.oldest_days == 45
    assert trit.stale is True  # the 45-day lot crossed the 30-day threshold
    assert trit.any_estimated is True
    # Lots oldest-first (FIFO order); per-lot aging + flags survive the rollup.
    assert [(v.qty, v.days_held, v.stale, v.cost_is_estimated) for v in trit.lots] == [
        (100, 45, True, False), (50, 2, False, True),
    ]
    assert (pye.stale, pye.any_estimated) == (False, False)


async def test_inventory_uses_landed_cost_with_write_down_floor():
    corp_id = await _seed_corp()
    lot_id = await _lot(corp_id, qty=10, cost="4.00",
                        unit_hauling_cost=Decimal("0.50"))
    # Write-downs are taken by #153's use case; set the column directly here.
    async with SessionLocal() as session:
        await session.execute(
            update(Lot).where(Lot.id == lot_id).values(written_down_to=Decimal("3.10"))
        )
        await session.commit()

    async with SessionLocal() as session:
        view = await lots_app.get_inventory(
            session, corporation_eve_id=CORP_ID, stale_days=30,
            sales_tax_rate=Decimal(0), now=NOW
        )

    # Carried at the written-down value, not purchase + hauling.
    assert view.items[0].lots[0].unit_cost == Decimal("3.10")
    assert view.total_cost == Decimal("31.00")


async def test_empty_inventory_is_all_zeros():
    await _seed_corp()
    async with SessionLocal() as session:
        view = await lots_app.get_inventory(
            session, corporation_eve_id=CORP_ID, stale_days=30,
            sales_tax_rate=Decimal(0), now=NOW
        )
    assert view.total_cost == Decimal(0)
    assert view.items == []


async def _configure_market(*, prices: dict[int, str]) -> None:
    """Give the corp a default hub (Jita, buy percentile) and cache buy prices."""
    async with SessionLocal() as session:
        corp = await corporations_repo.get_by_eve_id(session, CORP_ID)
        await config_repo.upsert_config(
            session,
            corporation_id=corp.id,
            market_hub_id=JITA,
            default_basis="buy",
            default_percentage=90,
            aggregate_field="percentile",
        )
        for type_id, buy in prices.items():
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


# --- worth now + unrealized gain/loss (#153) ---------------------------------------


async def test_worth_and_unrealized_from_the_cached_market():
    corp_id = await _seed_corp()
    await _lot(corp_id, type_id=34, qty=100, cost="4.00")  # cost 400
    await _lot(corp_id, type_id=35, qty=10, cost="2.00")  # cost 20, no cached price
    # Jita buy 5.00, 10% tax → nrv 4.50/unit → worth 450, paper gain +50.
    await _configure_market(prices={34: "5.00"})

    async with SessionLocal() as session:
        view = await lots_app.get_inventory(
            session, corporation_eve_id=CORP_ID, stale_days=30,
            sales_tax_rate=Decimal("0.10"), now=NOW,
        )

    trit = next(i for i in view.items if i.type_id == 34)
    assert trit.worth == Decimal("450.00")
    assert trit.unrealized == Decimal("50.00")
    # The unpriced type is surfaced, never invented.
    pye = next(i for i in view.items if i.type_id == 35)
    assert pye.worth is None and pye.unrealized is None
    assert view.unpriced_types == 1
    # Totals cover only priced items; unrealized is its own line, not folded in.
    assert view.worth_total == Decimal("450.00")
    assert view.unrealized_total == Decimal("50.00")
    assert view.total_cost == Decimal("420.00")


async def test_unrealized_loss_is_negative():
    corp_id = await _seed_corp()
    await _lot(corp_id, type_id=34, qty=100, cost="4.00")
    await _configure_market(prices={34: "3.00"})

    async with SessionLocal() as session:
        view = await lots_app.get_inventory(
            session, corporation_eve_id=CORP_ID, stale_days=30,
            sales_tax_rate=Decimal(0), now=NOW,
        )

    assert view.items[0].unrealized == Decimal("-100.00")
    assert view.unrealized_total == Decimal("-100.00")


# --- API gates + shape --------------------------------------------------------------


async def test_endpoint_returns_inventory_for_entitled_manager():
    corp_id = await _seed_corp()
    # The endpoint runs on the REAL clock, so anchor this lot to it (a fixed NOW
    # here was a time bomb: the gap grows a day every day the suite ages).
    await _lot(corp_id, qty=100, cost="4.00",
               acquired_at=datetime.now(UTC) - timedelta(days=3))
    async with make_client(CeoEsi()) as http:
        await login(http)
        resp = await http.get("/api/v1/corporations/me/accounting/inventory")
    assert resp.status_code == 200
    body = resp.json()
    assert Decimal(body["total_cost"]) == Decimal("400.00")
    assert body["items"][0]["type_name"] == "Tritanium"
    assert body["items"][0]["lots"][0]["days_held"] == 3


async def test_endpoint_402_without_entitlement():
    await _seed_corp(entitled=False)
    async with make_client(CeoEsi()) as http:
        await login(http)
        resp = await http.get("/api/v1/corporations/me/accounting/inventory")
    assert resp.status_code == 402


async def test_endpoint_403_for_plain_member():
    await _seed_corp()
    async with make_client(MemberEsi()) as http:
        await login(http)
        resp = await http.get("/api/v1/corporations/me/accounting/inventory")
    assert resp.status_code == 403
