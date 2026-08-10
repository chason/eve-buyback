"""#158 / ADR-0045: the manual-entry escape hatch — off-game sales through the
shared FIFO path, known-cost lots with source/confidence kept distinct, expense
corrections as reversing entries, and the wrong-wallet receivable's create → clear
loop (no revenue, no double count)."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.data.db import SessionLocal
from app.data.repositories import corporations as corporations_repo
from app.data.repositories import entitlements as entitlements_repo
from app.data.repositories import expenses as expenses_repo
from app.data.repositories import lots as lots_repo
from app.data.repositories import reconciliation as recon_repo
from app.data.repositories import sales as sales_repo
from app.main import app
from tests.helpers import CHAR_ID, CORP_ID, CeoEsi, MemberEsi, login, make_client

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
JITA = "60003760"
TRIT = 34
BASE = "/api/v1/corporations/me/accounting"


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


async def _seed(*, entitled: bool = True):
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
        await session.commit()
        return corp.id


async def _lot(corp_id, *, qty=100, cost="3.60"):
    async with SessionLocal() as session:
        lot = await lots_repo.create_lot(
            session, corporation_id=corp_id, item_type_id=TRIT, qty=qty,
            unit_purchase_cost=Decimal(cost),
            acquired_at=NOW - timedelta(days=5),
            source="buyback", location_id=JITA,
        )
        await session.commit()
        return lot


# --- manual sale -------------------------------------------------------------------


async def test_manual_sale_consumes_fifo_with_manual_provenance():
    corp_id = await _seed()
    lot = await _lot(corp_id, qty=100)
    async with make_client(CeoEsi()) as http:
        await login(http)
        resp = await http.post(f"{BASE}/manual/sale", json={
            "type_id": TRIT, "qty": 60, "unit_proceeds": "5.00",
            "location_id": JITA, "note": "Sold to Renn on comms",
        })
    assert resp.status_code == 200
    assert resp.json() == {"stock_was_missing": False}

    async with SessionLocal() as session:
        (sale,) = await sales_repo.list_for_corp(session, corporation_id=corp_id)
        lots = await lots_repo.open_lots(session, corporation_id=corp_id)
    assert (sale.lot_id, sale.qty, sale.unit_proceeds) == (
        lot.id, 60, Decimal("5.00"),
    )
    # Provenance vs cost-confidence, never conflated: a manual sale against a
    # measured lot is manual AND measured.
    assert sale.channel == "direct"
    assert sale.source == "manual"
    assert sale.recorded_by_character_id == CHAR_ID
    assert sale.note == "Sold to Renn on comms"
    assert lots[0].qty_remaining == 40


async def test_manual_sale_of_unknown_stock_flags_and_reports():
    corp_id = await _seed()
    async with make_client(CeoEsi()) as http:
        await login(http)
        resp = await http.post(f"{BASE}/manual/sale", json={
            "type_id": TRIT, "qty": 10, "unit_proceeds": "5.00",
            "location_id": JITA, "note": "off-game deal",
        })
    assert resp.json() == {"stock_was_missing": True}
    async with SessionLocal() as session:
        events = await recon_repo.list_for_corp(session, corporation_id=corp_id)
    assert events[0].kind == "unmatched_sale" and events[0].flagged is True


async def test_manual_sale_requires_a_note():
    await _seed()
    async with make_client(CeoEsi()) as http:
        await login(http)
        resp = await http.post(f"{BASE}/manual/sale", json={
            "type_id": TRIT, "qty": 10, "unit_proceeds": "5.00",
            "location_id": JITA, "note": "",
        })
    assert resp.status_code == 422


# --- manual lot --------------------------------------------------------------------


async def test_manual_lot_keeps_source_and_confidence_distinct():
    corp_id = await _seed()
    async with make_client(CeoEsi()) as http:
        await login(http)
        resp = await http.post(f"{BASE}/manual/lot", json={
            "type_id": TRIT, "qty": 500, "unit_cost": "3.75",
            "location_id": JITA, "note": "bought from an alt corp, price known",
            "cost_is_estimated": False,
        })
    assert resp.status_code == 201
    async with SessionLocal() as session:
        (lot,) = await lots_repo.open_lots(session, corporation_id=corp_id)
    assert lot.source == "manual"  # provenance: entered by hand…
    assert lot.cost_is_estimated is False  # …but the cost is measured, not deemed
    assert lot.unit_purchase_cost == Decimal("3.75")
    assert lot.notes == "bought from an alt corp, price known"


# --- manual expense + reversing correction -----------------------------------------


async def test_expense_and_reversing_correction_never_edit():
    corp_id = await _seed()
    async with make_client(CeoEsi()) as http:
        await login(http)
        resp = await http.post(f"{BASE}/manual/expense", json={
            "kind": "hauling", "amount": "250000.00",
            "note": "JF run to Amarr",
        })
        assert resp.status_code == 201
        # Oops — wrong amount. The fix is a REVERSING entry, not an edit.
        resp = await http.post(f"{BASE}/manual/expense", json={
            "kind": "hauling", "amount": "-250000.00",
            "note": "reverses: JF run to Amarr (typo, re-entering)",
        })
        assert resp.status_code == 201

    async with SessionLocal() as session:
        rows = await expenses_repo.list_for_corp(session, corporation_id=corp_id)
    # Both entries stand; the net is zero; the trail says why.
    assert len(rows) == 2
    assert sum(r.amount for r in rows) == Decimal(0)
    assert all(r.source == "manual" for r in rows)


# --- receivables -------------------------------------------------------------------


async def test_receivable_create_and_clear_loop():
    await _seed()
    async with make_client(CeoEsi()) as http:
        await login(http)
        resp = await http.post(f"{BASE}/receivables", json={
            "amount": "50000000.00",
            "note": "Buyer paid into division 1 by mistake",
        })
        assert resp.status_code == 201
        rid = resp.json()["id"]

        resp = await http.get(f"{BASE}/receivables")
        (row,) = resp.json()
        assert row["cleared_at"] is None
        assert Decimal(row["amount"]) == Decimal("50000000.00")

        resp = await http.post(f"{BASE}/receivables/{rid}/clear", json={
            "note": "moved to division 3 today",
        })
        assert resp.status_code == 200
        assert resp.json()["cleared_at"] is not None
        assert resp.json()["cleared_note"] == "moved to division 3 today"

        # Clearing again is a no-op (the end state already holds).
        resp = await http.post(f"{BASE}/receivables/{rid}/clear", json={})
        assert resp.status_code == 200

        # A receivable is never revenue: no sale rows came from any of this.
    async with SessionLocal() as session:
        corp = await corporations_repo.get_by_eve_id(session, CORP_ID)
        assert await sales_repo.list_for_corp(session, corporation_id=corp.id) == []


async def test_clear_unknown_receivable_is_404():
    await _seed()
    async with make_client(CeoEsi()) as http:
        await login(http)
        resp = await http.post(
            f"{BASE}/receivables/{uuid.uuid4()}/clear", json={}
        )
    assert resp.status_code == 404


# --- gates -------------------------------------------------------------------------


async def test_manual_entries_are_entitlement_gated_and_manager_only():
    await _seed(entitled=False)
    async with make_client(CeoEsi()) as http:
        await login(http)
        resp = await http.post(f"{BASE}/manual/expense", json={
            "kind": "other", "amount": "1.00", "note": "nope",
        })
    assert resp.status_code == 402

    app.dependency_overrides.clear()
    async with make_client(MemberEsi()) as http:
        await login(http)
        resp = await http.get(f"{BASE}/receivables")
    assert resp.status_code == 403
