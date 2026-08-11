"""#205 / ADR-0049: an optional hauling cost on the move confirmation. Booked
as a SELLING expense attributed to the move (corp freight is a selling cost,
ADR-0043/0045) — never landed cost: the moved lots' carrying value must not
change. Omitting the cost books nothing extra."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.application import moves as moves_app
from app.data.db import SessionLocal
from app.data.repositories import corporations as corporations_repo
from app.data.repositories import entitlements as entitlements_repo
from app.data.repositories import expenses as expenses_repo
from app.data.repositories import hangars as hangars_repo
from app.data.repositories import lots as lots_repo
from app.data.repositories import move_suggestions as moves_repo
from app.data.repositories import reconciliation as recon_repo
from app.data.repositories import sde as sde_repo
from app.main import app
from tests.helpers import CHAR_ID, CORP_ID, CeoEsi, login, make_client

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
JITA = "60003760"
AMARR = "60008494"
TRIT = 34


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


async def _seed_corp() -> None:
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
        await sde_repo.bulk_upsert_types(session, [
            {"type_id": TRIT, "name": "Tritanium", "group_id": 18,
             "market_group_id": 1857, "volume": 0.01, "published": True},
        ])
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
    hauling: str = "0",
):
    async with SessionLocal() as session:
        corp = await corporations_repo.get_by_eve_id(session, CORP_ID)
        lot = await lots_repo.create_lot(
            session, corporation_id=corp.id, item_type_id=type_id, qty=qty,
            unit_purchase_cost=Decimal(unit_cost),
            unit_hauling_cost=Decimal(hauling), acquired_at=acquired_at,
            source=source, cost_is_estimated=estimated, location_id=location_id,
        )
        await session.commit()
    return lot


async def _suggestion(qty: int, deemed_lot_id):
    async with SessionLocal() as session:
        corp = await corporations_repo.get_by_eve_id(session, CORP_ID)
        shortfall = await recon_repo.add_event(
            session, corporation_id=corp.id, location_id=JITA, type_id=TRIT,
            kind="shortfall", qty=qty, occurred_at=NOW, flagged=True,
        )
        record = await moves_repo.add(
            session, corporation_id=corp.id, type_id=TRIT,
            origin_location_id=JITA, destination_location_id=AMARR, qty=qty,
            shortfall_event_id=shortfall.id, excess_lot_id=deemed_lot_id,
        )
        await session.commit()
    return record.id


async def _confirm(suggestion_id, *, haul_cost: str | None = None):
    async with SessionLocal() as session:
        return await moves_app.confirm_move(
            session, corporation_eve_id=CORP_ID, suggestion_id=suggestion_id,
            confirmed_by_character_id=CHAR_ID, confirmed_by_name="Boss",
            haul_cost=Decimal(haul_cost) if haul_cost is not None else None,
            now=NOW,
        )


async def _state():
    async with SessionLocal() as session:
        corp = await corporations_repo.get_by_eve_id(session, CORP_ID)
        lots = await lots_repo.open_lots(session, corporation_id=corp.id)
        expenses = await expenses_repo.list_for_corp(
            session, corporation_id=corp.id
        )
    return lots, expenses


# --- the expense books, attributed ---------------------------------------------------


async def test_haul_cost_books_a_hauling_expense_attributed_to_the_move():
    await _seed_corp()
    origin = await _lot(TRIT, 400, JITA, acquired_at=NOW - timedelta(days=5))
    deemed = await _lot(TRIT, 350, AMARR, unit_cost="4.00",
                        source="opening_balance", estimated=True)
    sid = await _suggestion(350, deemed.id)

    result = await _confirm(sid, haul_cost="1500000.50")
    assert result.qty_moved == 350

    lots, expenses = await _state()
    (expense,) = expenses
    assert expense.kind == "hauling"
    assert expense.amount == Decimal("1500000.50")
    assert expense.source == "manual"
    assert expense.recorded_by_character_id == CHAR_ID
    assert expense.incurred_at == NOW
    # One lot relocated (the 350 split off the 400) -> attributed directly.
    (relocated,) = _lots_at(lots, AMARR)
    assert expense.lot_id == relocated.id
    assert relocated.source_lot_id == origin.id
    # The note names the move in plain words.
    assert expense.note == (
        "Hauling for the move of 350 Tritanium from Jita IV - Moon 4 "
        "to Amarr VIII - Emperor Family Academy"
    )


def _lots_at(lots, location_id: str):
    return [lot for lot in lots if lot.location_id == location_id]


async def test_multi_lot_move_attributes_through_the_note():
    await _seed_corp()
    await _lot(TRIT, 100, JITA, acquired_at=NOW - timedelta(days=30))
    await _lot(TRIT, 300, JITA, acquired_at=NOW - timedelta(days=10))
    deemed = await _lot(TRIT, 350, AMARR, unit_cost="4.00",
                        source="opening_balance", estimated=True)
    sid = await _suggestion(350, deemed.id)

    await _confirm(sid, haul_cost="2000000")

    _, expenses = await _state()
    (expense,) = expenses
    # Two lots relocated: no single lot to pin the ISK on - the note carries
    # the attribution instead (see confirm_move's docstring).
    assert expense.lot_id is None
    assert "move of 350 Tritanium" in expense.note


# --- the carrying-value invariant ----------------------------------------------------


async def test_freight_never_changes_the_moved_lots_carrying_value():
    await _seed_corp()
    origin = await _lot(TRIT, 400, JITA, unit_cost="5.2501", hauling="0.30",
                        acquired_at=NOW - timedelta(days=5))
    deemed = await _lot(TRIT, 400, AMARR, unit_cost="4.00",
                        source="opening_balance", estimated=True)
    sid = await _suggestion(400, deemed.id)

    await _confirm(sid, haul_cost="9999999")

    lots, _ = await _state()
    # The whole lot relocated in place: same row, byte-identical cost fields -
    # the freight went to an expense, not into the lot's basis.
    moved = next(lot for lot in lots if lot.id == origin.id)
    assert moved.location_id == AMARR
    assert moved.unit_purchase_cost == origin.unit_purchase_cost
    assert moved.unit_hauling_cost == origin.unit_hauling_cost
    assert moved.written_down_to == origin.written_down_to
    assert moved.acquired_at == origin.acquired_at


# --- omitted / nothing-moved paths ---------------------------------------------------


async def test_omitting_the_cost_books_nothing_extra():
    await _seed_corp()
    await _lot(TRIT, 400, JITA, acquired_at=NOW - timedelta(days=5))
    deemed = await _lot(TRIT, 350, AMARR, unit_cost="4.00",
                        source="opening_balance", estimated=True)
    sid = await _suggestion(350, deemed.id)

    await _confirm(sid)

    _, expenses = await _state()
    assert expenses == []


async def test_zero_cost_and_a_no_op_confirm_book_nothing():
    await _seed_corp()
    await _lot(TRIT, 400, JITA, acquired_at=NOW - timedelta(days=5))
    deemed = await _lot(TRIT, 350, AMARR, unit_cost="4.00",
                        source="opening_balance", estimated=True)
    sid = await _suggestion(350, deemed.id)

    await _confirm(sid, haul_cost="0")  # a free haul is not an expense
    _, expenses = await _state()
    assert expenses == []

    await _confirm(sid, haul_cost="500000")  # double confirm moves nothing
    _, expenses = await _state()
    assert expenses == []


# --- the API -------------------------------------------------------------------------


async def test_confirm_endpoint_accepts_the_optional_haul_cost():
    await _seed_corp()
    await _lot(TRIT, 400, JITA, acquired_at=NOW - timedelta(days=5))
    deemed = await _lot(TRIT, 350, AMARR, unit_cost="4.00",
                        source="opening_balance", estimated=True)
    sid = await _suggestion(350, deemed.id)

    async with make_client(CeoEsi()) as http:
        await login(http)
        resp = await http.post(
            f"/api/v1/corporations/me/accounting/move-suggestions/"
            f"{sid}/confirm",
            json={"haul_cost": "750000.25"},
        )
    assert resp.status_code == 204
    _, expenses = await _state()
    (expense,) = expenses
    assert (expense.kind, expense.amount) == ("hauling", Decimal("750000.25"))


async def test_confirm_endpoint_still_works_without_a_body():
    await _seed_corp()
    await _lot(TRIT, 400, JITA, acquired_at=NOW - timedelta(days=5))
    deemed = await _lot(TRIT, 350, AMARR, unit_cost="4.00",
                        source="opening_balance", estimated=True)
    sid = await _suggestion(350, deemed.id)

    async with make_client(CeoEsi()) as http:
        await login(http)
        resp = await http.post(
            f"/api/v1/corporations/me/accounting/move-suggestions/"
            f"{sid}/confirm"
        )
    assert resp.status_code == 204
    _, expenses = await _state()
    assert expenses == []
