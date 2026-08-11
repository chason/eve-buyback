"""#204 / ADR-0049: confirming a move whose deemed-cost lot was already partly
consumed — sold or transformed (ADR-0047) — before anyone confirmed. Only the
unconsumed remainder converts; the consumed units are spoken for and stay put:
sale rows keep their frozen deemed COGS (still flagged estimated), child lots
keep the deemed cost that flowed into them (still flagged estimated) — no
retroactive re-costing, no cascade. The card shows the convertible remainder,
never the stale paired quantity."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.application import moves as moves_app
from app.application import reconciliation as recon_app
from app.application import sales as sales_app
from app.application import transformations as transformations_app
from app.data.db import SessionLocal
from app.data.repositories import corporations as corporations_repo
from app.data.repositories import entitlements as entitlements_repo
from app.data.repositories import hangars as hangars_repo
from app.data.repositories import lots as lots_repo
from app.data.repositories import move_suggestions as moves_repo
from app.data.repositories import reconciliation as recon_repo
from app.data.repositories import sales as sales_repo
from app.main import app
from tests.helpers import CHAR_ID, CORP_ID, CeoEsi, login, make_client

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
JITA = "60003760"
AMARR = "60008494"
TRIT = 34
PYE = 35


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


async def _seed_corp() -> None:
    """Registered, entitled corp with marked hangars at Jita and Amarr. No ESI —
    every path here reads and writes the ledger only."""
    async with SessionLocal() as session:
        corp = await corporations_repo.create_corporation(
            session, eve_corporation_id=CORP_ID, name="Test Corp",
            ceo_character_id=CHAR_ID, registered_by_character_id=CHAR_ID,
        )
        await entitlements_repo.upsert(
            session, corporation_id=corp.id, feature="accounting",
            source="admin", expires_at=None,
        )
        await hangars_repo.add(
            session, corporation_id=corp.id, location_id=JITA,
            location_name="Jita IV - Moon 4", division=2,
        )
        await hangars_repo.add(
            session, corporation_id=corp.id, location_id=AMARR,
            location_name="Amarr VIII - Emperor Family Academy", division=2,
        )
        await session.commit()


async def _lot(
    type_id: int,
    qty: int,
    location_id: str,
    *,
    unit_cost: str = "5.25",
    acquired_at: datetime = NOW,
    source: str = "buyback",
    estimated: bool = False,
):
    async with SessionLocal() as session:
        corp = await corporations_repo.get_by_eve_id(session, CORP_ID)
        lot = await lots_repo.create_lot(
            session, corporation_id=corp.id, item_type_id=type_id, qty=qty,
            unit_purchase_cost=Decimal(unit_cost), acquired_at=acquired_at,
            source=source, cost_is_estimated=estimated, location_id=location_id,
        )
        await session.commit()
    return lot


async def _deemed_and_suggestion(qty: int, *, unit_cost: str = "4.00"):
    """The world as the #200 sync left it: a deemed-cost excess lot at Amarr, a
    flagged shortfall at Jita, and the pending pair decorating both."""
    deemed = await _lot(TRIT, qty, AMARR, unit_cost=unit_cost,
                        source="opening_balance", estimated=True)
    async with SessionLocal() as session:
        corp = await corporations_repo.get_by_eve_id(session, CORP_ID)
        shortfall = await recon_repo.add_event(
            session, corporation_id=corp.id, location_id=JITA, type_id=TRIT,
            kind="shortfall", qty=qty, occurred_at=NOW, flagged=True,
        )
        record = await moves_repo.add(
            session, corporation_id=corp.id, type_id=TRIT,
            origin_location_id=JITA, destination_location_id=AMARR, qty=qty,
            shortfall_event_id=shortfall.id, excess_lot_id=deemed.id,
        )
        await session.commit()
    return deemed, record.id


async def _sell_from_deemed(qty: int, *, unit_proceeds: str = "6.50") -> None:
    """A sale at the destination BEFORE anyone confirmed: FIFO lands on the
    deemed lot (the only Tritanium at Amarr), booking sale rows that snapshot
    its estimated cost."""
    async with SessionLocal() as session:
        corp = await corporations_repo.get_by_eve_id(session, CORP_ID)
        await sales_app.consume_and_record_sale(
            session, corp.id, type_id=TRIT, qty=qty,
            unit_proceeds=Decimal(unit_proceeds), location_id=AMARR,
            channel="direct", sold_at=NOW, now=NOW, source="manual",
            note="sold before the confirm",
        )
        await session.commit()


async def _reprocess_deemed(lot_id, qty: int, outputs: dict[int, int]) -> None:
    """A recorded reprocess of the deemed lot BEFORE anyone confirmed
    (ADR-0047): its estimated cost flows into the child material lots."""
    async with SessionLocal() as session:
        await transformations_app.record_reprocess(
            session, corporation_eve_id=CORP_ID, lot_id=lot_id, qty=qty,
            outputs=outputs, recorded_by_character_id=CHAR_ID, now=NOW,
        )


async def _confirm(suggestion_id):
    async with SessionLocal() as session:
        return await moves_app.confirm_move(
            session, corporation_eve_id=CORP_ID, suggestion_id=suggestion_id,
            confirmed_by_character_id=CHAR_ID, confirmed_by_name="Boss",
            now=NOW,
        )


async def _state():
    async with SessionLocal() as session:
        corp = await corporations_repo.get_by_eve_id(session, CORP_ID)
        lots = await lots_repo.open_lots(session, corporation_id=corp.id)
        sales = await sales_repo.list_for_corp(session, corporation_id=corp.id)
        events = await recon_repo.list_for_corp(session, corporation_id=corp.id)
    return lots, sales, events


# --- partly sold -------------------------------------------------------------------


async def test_partly_sold_converts_the_remainder_and_never_touches_sale_rows():
    await _seed_corp()
    await _lot(TRIT, 1000, JITA, unit_cost="5.25",
               acquired_at=NOW - timedelta(days=20))
    deemed, sid = await _deemed_and_suggestion(350)
    await _sell_from_deemed(100)
    _, sales_before, _ = await _state()

    result = await _confirm(sid)
    assert result.qty_moved == 250

    lots, sales_after, events = await _state()
    # Sale rows are frozen facts: byte-identical, still carrying the deemed
    # COGS, still flagged estimated — the confirm corrected nothing there.
    assert sales_after == sales_before
    (sale,) = sales_after
    assert (sale.lot_id, sale.qty) == (deemed.id, 100)
    assert sale.unit_cost == Decimal("4.00")
    assert sale.cost_is_estimated is True
    # Only the unconsumed remainder converted: the deemed lot is fully
    # reversed, and 250 units of real Jita cost now sit at Amarr.
    assert all(lot.id != deemed.id for lot in lots)
    at_amarr = [lot for lot in lots if lot.location_id == AMARR]
    assert sum(lot.qty_remaining for lot in at_amarr) == 250
    assert all(lot.cost_is_estimated is False for lot in at_amarr)
    assert all(lot.unit_purchase_cost == Decimal("5.25") for lot in at_amarr)
    logged = next(e for e in events if e.kind == "move_confirmed")
    assert logged.qty == 250


# --- partly transformed --------------------------------------------------------------


async def test_partly_transformed_converts_the_remainder_children_keep_their_cost():
    await _seed_corp()
    await _lot(TRIT, 1000, JITA, unit_cost="5.25",
               acquired_at=NOW - timedelta(days=20))
    deemed, sid = await _deemed_and_suggestion(350)
    # 100 units reprocessed at the deemed cost: 100 x 4.00 = 400.00 flows into
    # the 200-unit child (2.00/unit), inheriting cost_is_estimated.
    await _reprocess_deemed(deemed.id, 100, {PYE: 200})

    result = await _confirm(sid)
    assert result.qty_moved == 250

    lots, _, events = await _state()
    # The child lot keeps the deemed cost that flowed into it — still flagged
    # estimated, not re-costed, no cascade from the confirm.
    child = next(lot for lot in lots if lot.item_type_id == PYE)
    assert (child.qty_remaining, child.source_lot_id) == (200, deemed.id)
    assert child.unit_purchase_cost == Decimal("2.00")
    assert child.cost_is_estimated is True
    assert child.source == "reprocess"
    assert child.location_id == AMARR
    # The Tritanium remainder converted as usual.
    assert all(lot.id != deemed.id for lot in lots)
    moved = [
        lot
        for lot in lots
        if lot.location_id == AMARR and lot.item_type_id == TRIT
    ]
    assert sum(lot.qty_remaining for lot in moved) == 250
    assert all(lot.cost_is_estimated is False for lot in moved)
    logged = next(e for e in events if e.kind == "move_confirmed")
    assert logged.qty == 250


# --- the combination -----------------------------------------------------------------


async def test_partly_sold_and_partly_transformed_converts_whats_left():
    await _seed_corp()
    await _lot(TRIT, 1000, JITA, unit_cost="5.25",
               acquired_at=NOW - timedelta(days=20))
    deemed, sid = await _deemed_and_suggestion(350)
    await _sell_from_deemed(100)
    await _reprocess_deemed(deemed.id, 100, {PYE: 200})
    _, sales_before, _ = await _state()

    result = await _confirm(sid)
    assert result.qty_moved == 150

    lots, sales_after, _ = await _state()
    assert sales_after == sales_before  # frozen, still estimated
    assert all(s.cost_is_estimated for s in sales_after)
    child = next(lot for lot in lots if lot.item_type_id == PYE)
    assert child.cost_is_estimated is True
    assert child.unit_purchase_cost == Decimal("2.00")
    moved = [
        lot
        for lot in lots
        if lot.location_id == AMARR and lot.item_type_id == TRIT
    ]
    assert sum(lot.qty_remaining for lot in moved) == 150
    assert all(lot.cost_is_estimated is False for lot in moved)


# --- the displayed quantity ----------------------------------------------------------


async def test_card_shows_the_convertible_remainder_not_the_stale_paired_qty():
    await _seed_corp()
    await _lot(TRIT, 1000, JITA)
    _, _sid = await _deemed_and_suggestion(350)
    await _sell_from_deemed(100)

    async with make_client(CeoEsi()) as http:
        await login(http)
        resp = await http.get(
            "/api/v1/corporations/me/accounting/move-suggestions"
        )
    (card,) = resp.json()
    assert card["qty"] == 250


async def test_fully_consumed_pair_shows_no_card_but_stays_pending():
    # Nothing left to convert here — the stranded sold remainder at the origin
    # is the sell-side already-sold pairing's business (ADR-0049), not a card
    # promising a zero-unit move.
    await _seed_corp()
    await _lot(TRIT, 1000, JITA)
    _, _sid = await _deemed_and_suggestion(350)
    await _sell_from_deemed(350)

    async with SessionLocal() as session:
        views = await recon_app.list_move_suggestions(
            session, corporation_eve_id=CORP_ID
        )
        corp = await corporations_repo.get_by_eve_id(session, CORP_ID)
        pending = await moves_repo.list_pending(session, corporation_id=corp.id)
    assert views == []
    assert len(pending) == 1  # the row itself is untouched — display only
