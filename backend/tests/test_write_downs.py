"""#153 / ADR-0043 conservatism: the automatic write-down sweep — floors carried
value to market when it drops below cost, books the loss once, never reverses on a
price rise, and books only the incremental loss on a further drop."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.application import lots as lots_app
from app.data.db import SessionLocal
from app.data.models import MarketPrice
from app.data.repositories import buyback_config as config_repo
from app.data.repositories import corporations as corporations_repo
from app.data.repositories import entitlements as entitlements_repo
from app.data.repositories import expenses as expenses_repo
from app.data.repositories import lots as lots_repo

NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)
CORP_ID = 98000001
JITA = "60003760"
TRIT = 34


async def _seed(*, qty: int = 100, cost: str = "4.00"):
    async with SessionLocal() as session:
        corp = await corporations_repo.create_corporation(
            session,
            eve_corporation_id=CORP_ID,
            name="Test Corp",
            ceo_character_id=1,
            registered_by_character_id=1,
        )
        await config_repo.upsert_config(
            session,
            corporation_id=corp.id,
            market_hub_id=JITA,
            default_basis="buy",
            default_percentage=90,
            aggregate_field="percentile",
        )
        # The sweep itself isn't gated (the job filters entitled corps); the grant
        # is for the gated `get_inventory` assertions.
        await entitlements_repo.upsert(
            session, corporation_id=corp.id, feature="accounting",
            source="admin", expires_at=None,
        )
        await lots_repo.create_lot(
            session,
            corporation_id=corp.id,
            item_type_id=TRIT,
            qty=qty,
            unit_purchase_cost=Decimal(cost),
            acquired_at=NOW,
            source="buyback",
        )
        await session.commit()
        return corp.id


async def _set_price(buy: str) -> None:
    async with SessionLocal() as session:
        b = Decimal(buy)
        existing = await session.get(MarketPrice, (JITA, TRIT))
        if existing is not None:
            await session.delete(existing)
            await session.flush()
        session.add(MarketPrice(
            hub_id=JITA, type_id=TRIT,
            buy_weighted_average=b, buy_max=b, buy_min=b, buy_median=b,
            buy_percentile=b, buy_volume=Decimal(1000), buy_order_count=10,
            sell_weighted_average=b, sell_max=b, sell_min=b, sell_median=b,
            sell_percentile=b, sell_volume=Decimal(1000), sell_order_count=10,
            fetched_at=NOW,
        ))
        await session.commit()


async def _sweep() -> int:
    async with SessionLocal() as session:
        return await lots_app.apply_write_downs(
            session, corporation_eve_id=CORP_ID,
            sales_tax_rate=Decimal(0), now=NOW,
        )


async def _state(corp_id):
    async with SessionLocal() as session:
        lots = await lots_repo.open_lots(session, corporation_id=corp_id)
        expenses = await expenses_repo.list_for_corp(session, corporation_id=corp_id)
    return lots[0], expenses


async def test_write_down_floors_cost_and_books_the_loss_once():
    corp_id = await _seed(qty=100, cost="4.00")
    await _set_price("3.00")

    assert await _sweep() == 1
    lot, expenses = await _state(corp_id)
    assert lot.written_down_to == Decimal("3.00")
    assert len(expenses) == 1
    assert expenses[0].kind == "write_down"
    assert expenses[0].source == "system"
    assert expenses[0].amount == Decimal("100.00")  # 100 × (4.00 − 3.00)
    assert expenses[0].lot_id == lot.id

    # Stable prices → idempotent: the floored cost equals NRV, nothing to book.
    assert await _sweep() == 0
    _, expenses = await _state(corp_id)
    assert len(expenses) == 1


async def test_price_rise_never_reverses_a_write_down():
    corp_id = await _seed(qty=100, cost="4.00")
    await _set_price("3.00")
    assert await _sweep() == 1

    await _set_price("5.00")
    assert await _sweep() == 0

    lot, expenses = await _state(corp_id)
    assert lot.written_down_to == Decimal("3.00")  # still floored
    assert len(expenses) == 1
    # The recovery shows as unrealized gain from the floored base, not restored cost.
    async with SessionLocal() as session:
        view = await lots_app.get_inventory(
            session, corporation_eve_id=CORP_ID, stale_days=30,
            sales_tax_rate=Decimal(0), now=NOW,
        )
    assert view.items[0].lots[0].unit_cost == Decimal("3.00")
    assert view.items[0].unrealized == Decimal("200.00")  # 100 × (5.00 − 3.00)


async def test_further_drop_books_only_the_incremental_loss():
    corp_id = await _seed(qty=100, cost="4.00")
    await _set_price("3.00")
    assert await _sweep() == 1

    await _set_price("2.50")
    assert await _sweep() == 1

    lot, expenses = await _state(corp_id)
    assert lot.written_down_to == Decimal("2.50")
    assert [e.amount for e in expenses] == [Decimal("100.00"), Decimal("50.00")]


async def test_no_cached_price_books_nothing():
    await _seed()
    assert await _sweep() == 0


async def test_write_down_repo_refuses_to_raise_the_floor():
    corp_id = await _seed()
    await _set_price("3.00")
    assert await _sweep() == 1
    async with SessionLocal() as session:
        lots = await lots_repo.open_lots(session, corporation_id=corp_id)
        with pytest.raises(ValueError, match="would raise it"):
            await lots_repo.write_down(
                session, lot_id=lots[0].id, value=Decimal("3.50")
            )
