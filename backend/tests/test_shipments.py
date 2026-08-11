"""#208 / ADR-0049: declared shipments — the disciplined path that preempts the
move heuristic. Recording a haul takes the quantity out of idle at the origin
and speaks for it at the destination, so a sync during transit flags nothing at
either end and never proposes a move for it; marking arrival relocates the
origin's oldest lots FIFO with cost basis, aging, and flags intact. Both steps
log; everything is entitlement-gated."""

import uuid as uuid_mod
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.application import corp_esi_token as corp_esi_token_app
from app.application import reconciliation as recon_app
from app.application import shipments as shipments_app
from app.application.auth import AuthenticatedUser
from app.application.errors import (
    EntitlementRequired,
    ShipmentAlreadyArrived,
    ShipmentHangarUnknown,
    ShipmentNotFound,
    ShipmentSameHangar,
    ShipmentStockUnavailable,
)
from app.data.db import SessionLocal
from app.data.models import MarketPrice
from app.data.repositories import buyback_config as config_repo
from app.data.repositories import buyback_locations as locations_repo
from app.data.repositories import corporations as corporations_repo
from app.data.repositories import entitlements as entitlements_repo
from app.data.repositories import hangars as hangars_repo
from app.data.repositories import lots as lots_repo
from app.data.repositories import move_suggestions as moves_repo
from app.data.repositories import reconciliation as recon_repo
from app.data.repositories import sde as sde_repo
from app.domain.reconciliation import Delta
from app.domain.shipments import absorb_open_shipments
from app.main import app
from app.plugins.sso import OAuthToken, VerifiedCharacter
from app.plugins.token_cipher import get_token_cipher
from tests.helpers import CHAR_ID, CORP_ID, CeoEsi, HangarAssetsEsi, MemberEsi, login, make_client

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
JITA = "60003760"
AMARR = "60008494"
TRIT = 34


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


# --- domain: open-shipment absorption ----------------------------------------------


def test_absorb_shrinks_only_matching_excess():
    deltas = [
        Delta(location_id=AMARR, type_id=TRIT, kind="excess", qty=400),
        Delta(location_id=AMARR, type_id=35, kind="excess", qty=10),
        Delta(location_id=JITA, type_id=TRIT, kind="shortfall", qty=400),
    ]
    out = absorb_open_shipments(deltas, {(AMARR, TRIT): 300})
    # The covered 300 vanish; the residual 100 keeps the default treatment;
    # other slots — and shortfalls — pass through untouched.
    assert out == [
        Delta(location_id=AMARR, type_id=TRIT, kind="excess", qty=100),
        Delta(location_id=AMARR, type_id=35, kind="excess", qty=10),
        Delta(location_id=JITA, type_id=TRIT, kind="shortfall", qty=400),
    ]
    # Fully covered → the delta disappears entirely; never a negative/shortfall.
    assert absorb_open_shipments(deltas[:1], {(AMARR, TRIT): 400}) == []
    assert absorb_open_shipments(deltas[:1], {(AMARR, TRIT): 999}) == []
    # The origin end is covered the same way (recorded but not yet picked up):
    # the in-transit subtraction lowered `expected`, so the still-sitting stock
    # reads as excess — absorbed, never booked as a deemed lot.
    origin_excess = [Delta(location_id=JITA, type_id=TRIT, kind="excess", qty=400)]
    assert absorb_open_shipments(origin_excess, {(JITA, TRIT): 400}) == []


# --- harness -----------------------------------------------------------------------


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


def _user() -> AuthenticatedUser:
    return AuthenticatedUser(
        character_id=CHAR_ID, character_name="Boss", corporation_id=CORP_ID,
        corporation_name="Test Corp", role="ceo", is_director=False,
        corporation_registered=True,
    )


async def _seed(
    esi: HangarAssetsEsi | None = None, *, entitled: bool = True
) -> None:
    """Registered corp with marked hangars at Jita and Amarr, Jita config +
    cached price (so an excess WOULD book a deemed lot if a shipment failed to
    preempt it), the Tritanium SDE row, and — when a fake ESI is given — a
    connected corp token for the sync."""
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
        await config_repo.upsert_config(
            session, corporation_id=corp.id, market_hub_id=JITA,
            default_basis="buy", default_percentage=90,
            aggregate_field="percentile",
        )
        for location_id, name in ((JITA, "Jita IV - Moon 4"), (AMARR, "Amarr VIII")):
            await locations_repo.add(
                session, corp.id, kind="npc_station", location_id=location_id,
                name=name, system_name=name.split(" ")[0],
            )
            await hangars_repo.add(
                session, corporation_id=corp.id, location_id=location_id,
                location_name=name, division=2,
            )
        await sde_repo.bulk_upsert_types(session, [
            {"type_id": TRIT, "name": "Tritanium", "group_id": 18,
             "market_group_id": 1857, "volume": 0.01, "published": True},
        ])
        b = Decimal("4.00")
        session.add(MarketPrice(
            hub_id=JITA, type_id=TRIT,
            buy_weighted_average=b, buy_max=b, buy_min=b, buy_median=b,
            buy_percentile=b, buy_volume=Decimal(1000), buy_order_count=10,
            sell_weighted_average=b, sell_max=b, sell_min=b, sell_median=b,
            sell_percentile=b, sell_volume=Decimal(1000), sell_order_count=10,
            fetched_at=NOW,
        ))
        await session.commit()
    if esi is not None:
        async with SessionLocal() as session:
            await corp_esi_token_app.complete_corp_esi_authorize(
                session, FakeSso(), esi, code="c", verifier="v",
                user=_user(), cipher=get_token_cipher(),
            )


async def _lot(
    qty: int,
    location_id: str,
    *,
    unit_cost: str = "5.25",
    acquired_at: datetime = NOW,
    estimated: bool = False,
):
    async with SessionLocal() as session:
        corp = await corporations_repo.get_by_eve_id(session, CORP_ID)
        lot = await lots_repo.create_lot(
            session, corporation_id=corp.id, item_type_id=TRIT, qty=qty,
            unit_purchase_cost=Decimal(unit_cost), acquired_at=acquired_at,
            source="buyback", cost_is_estimated=estimated,
            location_id=location_id,
        )
        await session.commit()
    return lot


async def _record(
    qty: int,
    *,
    origin: str = JITA,
    destination: str = AMARR,
    corporation_eve_id: int = CORP_ID,
):
    async with SessionLocal() as session:
        return await shipments_app.record_shipment(
            session, corporation_eve_id=corporation_eve_id, type_id=TRIT,
            qty=qty, origin_location_id=origin,
            destination_location_id=destination,
            recorded_by_character_id=CHAR_ID, recorded_by_name="Boss", now=NOW,
        )


async def _arrive(shipment_id, *, corporation_eve_id: int = CORP_ID):
    async with SessionLocal() as session:
        return await shipments_app.mark_arrived(
            session, corporation_eve_id=corporation_eve_id,
            shipment_id=shipment_id, marked_by_character_id=CHAR_ID,
            marked_by_name="Boss", now=NOW,
        )


async def _sync(esi: HangarAssetsEsi):
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
        pending = await moves_repo.list_pending(session, corporation_id=corp.id)
        open_hauls = await shipments_app.list_open_shipments(
            session, corporation_eve_id=CORP_ID
        )
    return lots, events, pending, open_hauls


# --- the lifecycle -----------------------------------------------------------------


async def test_open_shipment_hides_the_haul_from_syncs_at_both_ends():
    esi = HangarAssetsEsi()
    await _seed(esi)
    await _lot(1000, JITA, acquired_at=NOW - timedelta(days=30))

    await _record(400)

    # Recorded but not yet picked up: the goods still physically sit at the
    # origin — the sync must not read them as excess there (no deemed lot).
    esi.stock = {(JITA, TRIT): 1000}
    result = await _sync(esi)
    assert result.lots_added == 0 and result.flagged == 0

    # Mid-flight: the origin hangar is 400 short of the books, the destination
    # untouched — the sync must flag nothing and book nothing.
    esi.stock = {(JITA, TRIT): 600}
    result = await _sync(esi)
    assert result.lots_added == 0 and result.flagged == 0

    # Landed but not yet marked: the goods physically sit at the destination —
    # still nothing (they're spoken for, not off-app excess), and no pairing.
    esi.stock = {(JITA, TRIT): 600, (AMARR, TRIT): 400}
    result = await _sync(esi)
    assert result.lots_added == 0 and result.flagged == 0

    lots, events, pending, open_hauls = await _state()
    assert [lot.qty_remaining for lot in lots] == [1000]  # untouched, at Jita
    assert [e.kind for e in events] == ["shipment_recorded"]
    assert pending == []
    assert len(open_hauls) == 1
    haul = open_hauls[0]
    assert (haul.type_name, haul.origin_name) == ("Tritanium", "Jita IV - Moon 4")
    assert haul.destination_name == "Amarr VIII"


async def test_arrival_relocates_fifo_with_cost_and_aging_intact():
    esi = HangarAssetsEsi()
    await _seed(esi)
    old = await _lot(300, JITA, unit_cost="5.25",
                     acquired_at=NOW - timedelta(days=30))
    newer = await _lot(700, JITA, unit_cost="6.00",
                       acquired_at=NOW - timedelta(days=10))

    shipment = await _record(350)
    result = await _arrive(shipment.id)
    assert result.qty_moved == 350

    lots, events, _, open_hauls = await _state()
    assert open_hauls == []
    # FIFO relocation (the confirm_move mechanic): the oldest lot moved whole
    # (in place — same id, same aging), the newer one split for the last 50.
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
    assert (remaining_new.qty_remaining, remaining_new.location_id) == (650, JITA)
    # Carrying value never changes — a move is not an acquisition.
    amarr_value = sum(
        lot.qty_remaining * lot.unit_purchase_cost for lot in at_amarr
    )
    assert amarr_value == 300 * Decimal("5.25") + 50 * Decimal("6.00")

    # Both steps logged, each at the end where it happened, with who acted.
    recorded = next(e for e in events if e.kind == "shipment_recorded")
    assert (recorded.location_id, recorded.qty, recorded.flagged) == (JITA, 350, False)
    assert "Amarr VIII" in recorded.note and "by Boss" in recorded.note
    arrived = next(e for e in events if e.kind == "shipment_arrived")
    assert (arrived.location_id, arrived.qty, arrived.flagged) == (AMARR, 350, False)
    assert "Jita IV - Moon 4" in arrived.note and "by Boss" in arrived.note

    # The books now match the physical world — a sync sees nothing.
    esi.stock = {(JITA, TRIT): 650, (AMARR, TRIT): 350}
    result = await _sync(esi)
    assert result.lots_added == 0 and result.flagged == 0


async def test_shipment_preempts_the_pairing_and_residuals_keep_defaults():
    # 400 left Jita for Amarr but only 300 were declared: the declared 300 are
    # invisible to the sync, while the undeclared 100 get the full ADR-0044
    # default treatment — and only THEY may pair as a suggested move.
    esi = HangarAssetsEsi()
    await _seed(esi)
    await _lot(400, JITA, acquired_at=NOW - timedelta(days=30))
    await _record(300)

    esi.stock = {(JITA, TRIT): 0, (AMARR, TRIT): 400}
    result = await _sync(esi)

    lots, events, pending, _ = await _state()
    # The undeclared 100: a flagged shortfall at Jita, a deemed lot at Amarr.
    assert result.lots_added == 1 and result.flagged == 1
    shortfall = next(e for e in events if e.kind == "shortfall")
    assert (shortfall.location_id, shortfall.qty) == (JITA, 100)
    deemed = next(lot for lot in lots if lot.cost_is_estimated)
    assert (deemed.location_id, deemed.qty_remaining) == (AMARR, 100)
    # The pairing proposes only the undeclared overlap — never the haul.
    assert [s.qty for s in pending] == [100]


async def test_arrival_log_does_not_disturb_a_standing_shortfall():
    # An unrelated shortfall already flagged at the destination stays a single
    # standing flag: the shipment_arrived log entry at that slot is a manager
    # action, not slot state — the sync's dedupe must look past it, not re-flag
    # the unchanged shortfall (and churn the anchor id pairings key on).
    esi = HangarAssetsEsi()
    await _seed(esi)
    await _lot(30, AMARR)
    await _lot(1000, JITA)
    esi.stock = {(JITA, TRIT): 1000, (AMARR, TRIT): 0}
    result = await _sync(esi)
    assert result.flagged == 1  # the 30 missing at Amarr

    shipment = await _record(100)
    await _arrive(shipment.id)
    esi.stock = {(JITA, TRIT): 900, (AMARR, TRIT): 100}  # the 30 still missing
    result = await _sync(esi)
    assert result.lots_added == 0 and result.flagged == 0

    _, events, _, _ = await _state()
    shortfalls = [e for e in events if e.kind == "shortfall"]
    assert [(e.location_id, e.qty) for e in shortfalls] == [(AMARR, 30)]


async def test_recording_more_than_idle_stock_is_refused():
    await _seed()
    await _lot(1000, JITA)

    with pytest.raises(ShipmentStockUnavailable):
        await _record(1200)

    # Open hauls count against idle too: 600 on the road leaves only 400 free.
    await _record(600)
    with pytest.raises(ShipmentStockUnavailable):
        await _record(500)
    await _record(400)  # exactly what's left is fine


async def test_arrival_caps_at_what_the_origin_still_holds():
    # Stock sold out from under an open haul (mid-transit bookkeeping): the
    # arrival moves what's actually there and reports it — nothing is invented.
    await _seed()
    lot = await _lot(500, JITA)
    shipment = await _record(400)
    async with SessionLocal() as session:
        await lots_repo.consume(session, lot_id=lot.id, qty=450)
        await session.commit()

    result = await _arrive(shipment.id)
    assert result.qty_moved == 50

    lots, events, _, _ = await _state()
    assert [(lot.location_id, lot.qty_remaining) for lot in lots] == [(AMARR, 50)]
    arrived = next(e for e in events if e.kind == "shipment_arrived")
    assert arrived.qty == 50


# --- the typed errors --------------------------------------------------------------


async def test_record_requires_two_distinct_marked_hangars():
    await _seed()
    await _lot(100, JITA)

    with pytest.raises(ShipmentSameHangar):
        await _record(50, destination=JITA)
    # A location that isn't a marked hangar — either end — is refused.
    with pytest.raises(ShipmentHangarUnknown):
        await _record(50, origin="60000001")
    with pytest.raises(ShipmentHangarUnknown):
        await _record(50, destination="60000001")


async def test_unknown_cross_corp_and_double_arrive():
    await _seed()
    await _lot(100, JITA)
    shipment = await _record(100)
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

    with pytest.raises(ShipmentNotFound):
        await _arrive(uuid_mod.uuid4())
    # Another corp probing a real id gets the same absence, not a hint.
    with pytest.raises(ShipmentNotFound):
        await _arrive(shipment.id, corporation_eve_id=CORP_ID + 1)

    await _arrive(shipment.id)
    with pytest.raises(ShipmentAlreadyArrived):
        await _arrive(shipment.id)


async def test_every_action_requires_the_accounting_entitlement():
    await _seed(entitled=False)
    await _lot(100, JITA)

    with pytest.raises(EntitlementRequired):
        await _record(50)
    with pytest.raises(EntitlementRequired):
        await _arrive(uuid_mod.uuid4())
    with pytest.raises(EntitlementRequired):
        async with SessionLocal() as session:
            await shipments_app.list_open_shipments(
                session, corporation_eve_id=CORP_ID
            )


# --- the API -----------------------------------------------------------------------


async def test_shipment_endpoints_round_trip():
    await _seed()
    await _lot(500, JITA, acquired_at=NOW - timedelta(days=7))

    async with make_client(CeoEsi()) as http:
        await login(http)
        resp = await http.post(
            "/api/v1/corporations/me/accounting/shipments",
            json={
                "type_id": TRIT, "qty": 200,
                "origin_location_id": JITA, "destination_location_id": AMARR,
            },
        )
        assert resp.status_code == 201
        listed = await http.get("/api/v1/corporations/me/accounting/shipments")
        (haul,) = listed.json()
        assert (haul["qty"], haul["type_name"]) == (200, "Tritanium")
        assert haul["origin_name"] == "Jita IV - Moon 4"
        assert haul["destination_name"] == "Amarr VIII"
        resp = await http.post(
            f"/api/v1/corporations/me/accounting/shipments/{haul['id']}/arrived"
        )
        assert resp.status_code == 204
        after = await http.get("/api/v1/corporations/me/accounting/shipments")
        assert after.json() == []
        # Over-shipping surfaces as a 422 with the plain-English detail.
        resp = await http.post(
            "/api/v1/corporations/me/accounting/shipments",
            json={
                "type_id": TRIT, "qty": 9999,
                "origin_location_id": JITA, "destination_location_id": AMARR,
            },
        )
        assert resp.status_code == 422
        assert "sitting free" in resp.json()["detail"]


async def test_shipment_endpoints_are_manager_gated():
    await _seed()
    async with make_client(MemberEsi()) as http:
        await login(http)
        listed = await http.get("/api/v1/corporations/me/accounting/shipments")
        assert listed.status_code == 403
        resp = await http.post(
            "/api/v1/corporations/me/accounting/shipments",
            json={
                "type_id": TRIT, "qty": 1,
                "origin_location_id": JITA, "destination_location_id": AMARR,
            },
        )
        assert resp.status_code == 403
        resp = await http.post(
            f"/api/v1/corporations/me/accounting/shipments/"
            f"{uuid_mod.uuid4()}/arrived"
        )
        assert resp.status_code == 403
