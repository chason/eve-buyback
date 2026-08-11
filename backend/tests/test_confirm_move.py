"""#201 / ADR-0049: confirming a move — one unit of work that reverses the
deemed-cost excess lot at the destination, relocates the origin's oldest idle
lots FIFO (splitting a lot on a partial move, cost fields carried byte-identical),
supersedes the shortfall flag with a `move_confirmed` log entry recording who
confirmed, and marks the suggestion confirmed. Idempotent per suggestion."""

import uuid as uuid_mod
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.application import moves as moves_app
from app.application.errors import (
    EntitlementRequired,
    MoveSuggestionNotFound,
    MoveSuggestionNotPending,
)
from app.data.db import SessionLocal
from app.data.repositories import corporations as corporations_repo
from app.data.repositories import entitlements as entitlements_repo
from app.data.repositories import hangars as hangars_repo
from app.data.repositories import lots as lots_repo
from app.data.repositories import move_suggestions as moves_repo
from app.data.repositories import reconciliation as recon_repo
from app.main import app
from tests.helpers import CHAR_ID, CORP_ID, CeoEsi, MemberEsi, login, make_client

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
JITA = "60003760"
AMARR = "60008494"
TRIT = 34


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


async def _seed_corp(*, entitled: bool = True) -> None:
    """Registered corp with marked hangars at Jita and Amarr. No ESI needed —
    the confirm path reads and writes the ledger only."""
    async with SessionLocal() as session:
        corp = await corporations_repo.create_corporation(
            session, eve_corporation_id=CORP_ID, name="Test Corp",
            ceo_character_id=CHAR_ID, registered_by_character_id=CHAR_ID,
        )
        if entitled:
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


async def _corp_id():
    async with SessionLocal() as session:
        return (await corporations_repo.get_by_eve_id(session, CORP_ID)).id


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


async def _suggestion(qty: int, deemed_lot_id) -> uuid_mod.UUID:
    """The pairing as the #200 sync would have persisted it: a flagged shortfall
    at the origin, decorated by a pending suggestion keyed to the deemed lot."""
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


async def _confirm(suggestion_id, *, corporation_eve_id: int = CORP_ID):
    async with SessionLocal() as session:
        return await moves_app.confirm_move(
            session, corporation_eve_id=corporation_eve_id,
            suggestion_id=suggestion_id, confirmed_by_character_id=CHAR_ID,
            confirmed_by_name="Boss", now=NOW,
        )


async def _state():
    async with SessionLocal() as session:
        corp = await corporations_repo.get_by_eve_id(session, CORP_ID)
        lots = await lots_repo.open_lots(session, corporation_id=corp.id)
        events = await recon_repo.list_for_corp(session, corporation_id=corp.id)
        pending = await moves_repo.list_pending(session, corporation_id=corp.id)
    return lots, events, pending


# --- the happy path ----------------------------------------------------------------


async def test_confirm_reverses_relocates_fifo_resolves_and_logs():
    await _seed_corp()
    old = await _lot(TRIT, 300, JITA, unit_cost="5.25",
                     acquired_at=NOW - timedelta(days=30))
    newer = await _lot(TRIT, 700, JITA, unit_cost="6.00",
                       acquired_at=NOW - timedelta(days=10))
    deemed = await _lot(TRIT, 350, AMARR, unit_cost="4.00",
                        source="opening_balance", estimated=True)
    sid = await _suggestion(350, deemed.id)

    result = await _confirm(sid)
    assert result.qty_moved == 350

    lots, events, pending = await _state()
    # The reversing entry: the deemed-cost units are consumed back out entirely.
    assert all(lot.id != deemed.id for lot in lots)
    # FIFO relocation: the oldest lot moved whole (in place — same id, same
    # aging), the newer one split for the remaining 50.
    at_amarr = [lot for lot in lots if lot.location_id == AMARR]
    moved_old = next(lot for lot in at_amarr if lot.id == old.id)
    assert moved_old.qty_remaining == 300
    assert moved_old.acquired_at == old.acquired_at
    child = next(lot for lot in at_amarr if lot.id != old.id)
    assert (child.qty_remaining, child.source_lot_id) == (50, newer.id)
    assert child.unit_purchase_cost == Decimal("6.00")
    assert child.acquired_at == newer.acquired_at
    assert child.cost_is_estimated is False
    remaining_new = next(lot for lot in lots if lot.id == newer.id)
    assert remaining_new.qty_remaining == 650
    assert remaining_new.location_id == JITA
    # Carrying value at the destination is the ORIGIN's measured cost — the
    # estimate is gone, and nothing was re-priced on the way.
    amarr_value = sum(
        lot.qty_remaining * lot.unit_purchase_cost for lot in at_amarr
    )
    assert amarr_value == 300 * Decimal("5.25") + 50 * Decimal("6.00")
    assert all(lot.cost_is_estimated is False for lot in at_amarr)

    # The suggestion left the pending list; the conversion is on the log at the
    # origin slot — superseding the shortfall flag — with who confirmed.
    assert pending == []
    logged = next(e for e in events if e.kind == "move_confirmed")
    assert (logged.location_id, logged.qty, logged.flagged) == (JITA, 350, False)
    assert "by Boss" in logged.note
    assert "Amarr VIII - Emperor Family Academy" in logged.note


async def test_partial_split_carries_every_cost_field_byte_identical():
    await _seed_corp()
    parent = await _lot(TRIT, 500, JITA, unit_cost="5.2501", hauling="0.30",
                        acquired_at=NOW - timedelta(days=45), source="manual",
                        estimated=True)
    async with SessionLocal() as session:
        parent = await lots_repo.write_down(
            session, lot_id=parent.id, value=Decimal("4.10")
        )
        await session.commit()
    deemed = await _lot(TRIT, 200, AMARR, unit_cost="4.00",
                        source="opening_balance", estimated=True)
    sid = await _suggestion(200, deemed.id)

    await _confirm(sid)

    lots, _, _ = await _state()
    child = next(lot for lot in lots if lot.location_id == AMARR)
    fields = (
        "unit_purchase_cost", "unit_hauling_cost", "acquired_at", "source",
        "cost_is_estimated", "written_down_to",
    )
    for field in fields:
        assert getattr(child, field) == getattr(parent, field), field
    assert child.written_down_to == Decimal("4.10")
    assert child.source_lot_id == parent.id
    remaining = next(lot for lot in lots if lot.id == parent.id)
    assert remaining.qty_remaining == 300


async def test_partially_consumed_deemed_lot_converts_only_the_remainder():
    # The full sold/transformed edge handling is #204 — this slice converts
    # what is still unconsumed, nothing provenance-specific.
    await _seed_corp()
    await _lot(TRIT, 1000, JITA, acquired_at=NOW - timedelta(days=5))
    deemed = await _lot(TRIT, 350, AMARR, unit_cost="4.00",
                        source="opening_balance", estimated=True)
    async with SessionLocal() as session:
        await lots_repo.consume(session, lot_id=deemed.id, qty=100)
        await session.commit()
    sid = await _suggestion(350, deemed.id)

    result = await _confirm(sid)
    assert result.qty_moved == 250

    lots, events, _ = await _state()
    assert all(lot.id != deemed.id for lot in lots)  # remainder fully reversed
    at_amarr = [lot for lot in lots if lot.location_id == AMARR]
    assert sum(lot.qty_remaining for lot in at_amarr) == 250
    logged = next(e for e in events if e.kind == "move_confirmed")
    assert logged.qty == 250


# --- idempotency + the typed errors ------------------------------------------------


async def test_double_confirm_is_a_noop():
    await _seed_corp()
    await _lot(TRIT, 400, JITA, acquired_at=NOW - timedelta(days=5))
    deemed = await _lot(TRIT, 350, AMARR, unit_cost="4.00",
                        source="opening_balance", estimated=True)
    sid = await _suggestion(350, deemed.id)

    await _confirm(sid)
    lots_before, events_before, _ = await _state()

    again = await _confirm(sid)
    assert again.qty_moved == 0
    lots_after, events_after, _ = await _state()
    assert lots_after == lots_before
    assert len(events_after) == len(events_before)


async def test_confirming_a_dismissed_suggestion_is_a_conflict():
    await _seed_corp()
    deemed = await _lot(TRIT, 100, AMARR, source="opening_balance",
                        estimated=True)
    sid = await _suggestion(100, deemed.id)
    async with SessionLocal() as session:
        await moves_repo.set_status(
            session, suggestion_id=sid, status="dismissed"
        )
        await session.commit()

    with pytest.raises(MoveSuggestionNotPending):
        await _confirm(sid)


async def test_unknown_and_cross_corp_suggestions_are_indistinguishable():
    await _seed_corp()
    deemed = await _lot(TRIT, 100, AMARR, source="opening_balance",
                        estimated=True)
    sid = await _suggestion(100, deemed.id)
    async with SessionLocal() as session:
        other = await corporations_repo.create_corporation(
            session, eve_corporation_id=CORP_ID + 1, name="Other Corp",
            ceo_character_id=777, registered_by_character_id=777,
        )
        await entitlements_repo.upsert(
            session, corporation_id=other.id, feature="accounting",
            source="admin", expires_at=None,
        )
        await session.commit()

    with pytest.raises(MoveSuggestionNotFound):
        await _confirm(uuid_mod.uuid4())
    # Another corp probing a real id gets the same absence, not a hint.
    with pytest.raises(MoveSuggestionNotFound):
        await _confirm(sid, corporation_eve_id=CORP_ID + 1)


async def test_confirm_requires_the_accounting_entitlement():
    await _seed_corp(entitled=False)
    deemed = await _lot(TRIT, 100, AMARR, source="opening_balance",
                        estimated=True)
    sid = await _suggestion(100, deemed.id)

    with pytest.raises(EntitlementRequired):
        await _confirm(sid)


# --- the API -----------------------------------------------------------------------


async def test_confirm_endpoint_round_trip_clears_the_card():
    await _seed_corp()
    await _lot(TRIT, 400, JITA, acquired_at=NOW - timedelta(days=5))
    deemed = await _lot(TRIT, 350, AMARR, unit_cost="4.00",
                        source="opening_balance", estimated=True)
    await _suggestion(350, deemed.id)

    async with make_client(CeoEsi()) as http:
        await login(http)
        listed = await http.get(
            "/api/v1/corporations/me/accounting/move-suggestions"
        )
        (card,) = listed.json()
        assert card["qty"] == 350  # the DTO now carries the id to target
        resp = await http.post(
            f"/api/v1/corporations/me/accounting/move-suggestions/"
            f"{card['id']}/confirm"
        )
        assert resp.status_code == 204
        after = await http.get(
            "/api/v1/corporations/me/accounting/move-suggestions"
        )
    assert after.json() == []


async def test_confirm_endpoint_is_manager_gated():
    await _seed_corp()
    async with make_client(MemberEsi()) as http:
        await login(http)
        resp = await http.post(
            f"/api/v1/corporations/me/accounting/move-suggestions/"
            f"{uuid_mod.uuid4()}/confirm"
        )
    assert resp.status_code == 403
