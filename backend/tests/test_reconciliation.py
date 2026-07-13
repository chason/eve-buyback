"""#155 / ADR-0044: hangar reconciliation — the pure diff, deemed-cost lot creation
(idempotent on the delta), shortfall flags with no-spam logging, the 90%-Jita
fallback, the anomaly threshold, first-run opening-balance import, and the API
(log list, manual check, reconnect answer)."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.application import corp_esi_token as corp_esi_token_app
from app.application import reconciliation as recon_app
from app.application.auth import AuthenticatedUser
from app.data.db import SessionLocal
from app.data.models import MarketPrice
from app.data.repositories import buyback_config as config_repo
from app.data.repositories import buyback_locations as locations_repo
from app.data.repositories import corporations as corporations_repo
from app.data.repositories import entitlements as entitlements_repo
from app.data.repositories import hangars as hangars_repo
from app.data.repositories import lots as lots_repo
from app.data.repositories import pricing_rules as rules_repo
from app.data.repositories import reconciliation as recon_repo
from app.data.repositories import sde as sde_repo
from app.domain.reconciliation import Delta, reconcile
from app.main import app
from app.plugins.esi import CharacterInfo, CorporationAssetsForbidden, CorporationInfo
from app.plugins.sso import OAuthToken, VerifiedCharacter
from app.plugins.token_cipher import get_token_cipher
from tests.helpers import CHAR_ID, CORP_ID, CeoEsi, login, make_client

NOW = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
JITA = "60003760"
TRIT = 34
PYE = 35


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


# --- domain diff -------------------------------------------------------------------


def test_reconcile_diffs_per_slot_and_skips_matched():
    deltas = reconcile(
        counted={(JITA, 34): 150, (JITA, 35): 10, (JITA, 36): 5},
        expected={(JITA, 34): 100, (JITA, 35): 10, (JITA, 37): 20},
    )
    assert deltas == [
        Delta(location_id=JITA, type_id=34, kind="excess", qty=50),
        Delta(location_id=JITA, type_id=36, kind="excess", qty=5),
        Delta(location_id=JITA, type_id=37, kind="shortfall", qty=20),
    ]


def test_reconcile_nothing_when_books_match():
    assert reconcile({(JITA, 34): 100}, {(JITA, 34): 100}) == []


# --- reconciliation pass harness ------------------------------------------------------


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


class AssetsEsi:
    """ESI fake: token-connect validation + the assets read. `hangar` is the stock in
    the marked hangar (CorpSAG2 at Jita) as {type_id: qty}; set `forbid` to test the
    missing-scope path."""

    def __init__(self):
        self.hangar: dict[int, int] = {}
        self.forbid = False
        self._item_id = iter(range(7_000_000, 8_000_000))

    # --- token-connect validation ---
    async def get_character(self, character_id):
        return CharacterInfo(name="Boss", corporation_id=CORP_ID)

    async def get_character_corporation(self, character_id):
        return CORP_ID

    async def get_corporation(self, corporation_id):
        return CorporationInfo(name="Test Corp", ceo_id=CHAR_ID, ticker="T")

    async def get_character_roles(self, character_id, access_token):
        return []

    # --- the assets read ---
    async def get_corporation_assets(self, corporation_id, access_token):
        if self.forbid:
            raise CorporationAssetsForbidden()
        from app.plugins.esi import CorporationAsset

        return [
            CorporationAsset(
                item_id=next(self._item_id), type_id=tid, quantity=qty,
                location_id=int(JITA), location_flag="CorpSAG2",
            )
            for tid, qty in self.hangar.items()
        ]


def _user() -> AuthenticatedUser:
    return AuthenticatedUser(
        character_id=CHAR_ID, character_name="Boss", corporation_id=CORP_ID,
        corporation_name="Test Corp", role="ceo", is_director=False,
        corporation_registered=True,
    )


async def _seed(esi: AssetsEsi, *, prices: dict[int, str] | None = None) -> None:
    """Registered + entitled corp, Jita config (buy percentile, default 90%), a Jita
    drop-off + marked hangar (division 2), SDE types, cached prices, and a connected
    corp ESI token."""
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
        await locations_repo.add(
            session, corp.id, kind="npc_station", location_id=JITA,
            name="Jita IV - Moon 4", system_name="Jita",
        )
        await hangars_repo.add(
            session, corporation_id=corp.id, location_id=JITA,
            location_name="Jita IV - Moon 4", division=2,
        )
        await sde_repo.bulk_upsert_types(session, [
            {"type_id": TRIT, "name": "Tritanium", "group_id": 18,
             "market_group_id": 1857, "volume": 0.01, "published": True},
            {"type_id": PYE, "name": "Pyerite", "group_id": 18,
             "market_group_id": 1857, "volume": 0.01, "published": True},
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
    async with SessionLocal() as session:
        await corp_esi_token_app.complete_corp_esi_authorize(
            session, FakeSso(), esi, code="c", verifier="v",
            user=_user(), cipher=get_token_cipher(),
        )


async def _run(esi: AssetsEsi, *, threshold: int = 1_000_000_000):
    async with SessionLocal() as session:
        return await recon_app.reconcile_hangars(
            session, FakeSso(), esi, corporation_eve_id=CORP_ID,
            cipher=get_token_cipher(), excess_flag_isk=threshold, now=NOW,
        )


async def _state():
    async with SessionLocal() as session:
        corp = await corporations_repo.get_by_eve_id(session, CORP_ID)
        lots = await lots_repo.open_lots(session, corporation_id=corp.id)
        events = await recon_repo.list_for_corp(session, corporation_id=corp.id)
    return lots, events


# --- excess → deemed-cost lots ---------------------------------------------------------


async def test_first_run_imports_opening_stock_at_deemed_cost():
    esi = AssetsEsi()
    await _seed(esi, prices={TRIT: "4.00"})
    esi.hangar = {TRIT: 1000}

    result = await _run(esi)

    assert result.lots_added == 1 and result.flagged == 0
    lots, events = await _state()
    lot = lots[0]
    # Deemed: the corp's own buyback answer — default 90% × Jita buy 4.00 = 3.60.
    assert lot.unit_purchase_cost == Decimal("3.60")
    assert lot.cost_is_estimated is True
    assert lot.source == "opening_balance"
    assert lot.location_id == JITA
    assert (events[0].kind, events[0].qty, events[0].lot_id) == ("excess", 1000, lot.id)
    assert events[0].flagged is False

    # Idempotent on the delta: the booked lot IS the expected stock now.
    again = await _run(esi)
    assert again.lots_added == 0 and again.flagged == 0
    lots, events = await _state()
    assert len(lots) == 1 and len(events) == 1


async def test_matched_stock_is_never_repriced_and_excess_books_only_the_delta():
    esi = AssetsEsi()
    await _seed(esi, prices={TRIT: "4.00"})
    async with SessionLocal() as session:
        corp = await corporations_repo.get_by_eve_id(session, CORP_ID)
        await lots_repo.create_lot(
            session, corporation_id=corp.id, item_type_id=TRIT, qty=1000,
            unit_purchase_cost=Decimal("5.25"),  # exact, recorded cost
            acquired_at=NOW, source="buyback", location_id=JITA,
        )
        await session.commit()
    esi.hangar = {TRIT: 1500}

    result = await _run(esi)

    assert result.lots_added == 1
    lots, _ = await _state()
    by_cost = {lot.unit_purchase_cost: lot for lot in lots}
    # The matched 1000 keep their recorded 5.25 — never re-priced (ADR-0044).
    assert by_cost[Decimal("5.25")].qty_remaining == 1000
    assert by_cost[Decimal("5.25")].cost_is_estimated is False
    # Only the unexplained 500 got a deemed cost.
    assert by_cost[Decimal("3.60")].qty_remaining == 500
    assert by_cost[Decimal("3.60")].cost_is_estimated is True


async def test_pricing_rule_drives_deemed_cost_with_jita_fallback_for_blacklisted():
    esi = AssetsEsi()
    await _seed(esi, prices={TRIT: "4.00", PYE: "8.00"})
    async with SessionLocal() as session:
        corp = await corporations_repo.get_by_eve_id(session, CORP_ID)
        # Tritanium at a specific 75%; Pyerite blacklisted (accepted=False) → the
        # corp's rules give no price, so the ADR fallback (90% Jita buy) applies.
        await rules_repo.upsert_rule(
            session, corporation_id=corp.id, target_kind="type", target_id=TRIT,
            basis="buy", percentage=Decimal("75"), enabled=True,
            reprocess=False, compressed_only=False, accepted=True,
        )
        await rules_repo.upsert_rule(
            session, corporation_id=corp.id, target_kind="type", target_id=PYE,
            basis="buy", percentage=Decimal("90"), enabled=True,
            reprocess=False, compressed_only=False, accepted=False,
        )
        await session.commit()
    esi.hangar = {TRIT: 100, PYE: 100}

    await _run(esi)

    lots, _ = await _state()
    costs = {lot.item_type_id: lot.unit_purchase_cost for lot in lots}
    assert costs[TRIT] == Decimal("3.00")  # 75% × 4.00 (the corp's own rule)
    assert costs[PYE] == Decimal("7.20")  # 90% × Jita buy 8.00 (fallback)


async def test_unpriceable_excess_is_flagged_not_invented_then_booked_once_priced():
    esi = AssetsEsi()
    await _seed(esi)  # no cached prices at all
    esi.hangar = {TRIT: 500}

    result = await _run(esi)
    assert result.lots_added == 0 and result.flagged == 1
    lots, events = await _state()
    assert lots == []
    assert events[0].lot_id is None and events[0].flagged is True

    # Still unpriceable, same delta → not re-logged (no daily spam).
    await _run(esi)
    _, events = await _state()
    assert len(events) == 1

    # A price appears → the excess books normally on the next pass.
    async with SessionLocal() as session:
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
    result = await _run(esi)
    assert result.lots_added == 1
    lots, _ = await _state()
    assert lots[0].unit_purchase_cost == Decimal("3.60")


async def test_large_excess_is_booked_but_flagged():
    esi = AssetsEsi()
    await _seed(esi, prices={TRIT: "4.00"})
    esi.hangar = {TRIT: 1000}  # deemed value 3,600 ISK

    result = await _run(esi, threshold=3_000)

    assert result.lots_added == 1 and result.flagged == 1
    _, events = await _state()
    assert events[0].flagged is True and events[0].lot_id is not None


# --- shortfalls -----------------------------------------------------------------------


async def test_shortfall_flags_without_inventing_a_lot_and_does_not_spam():
    esi = AssetsEsi()
    await _seed(esi, prices={TRIT: "4.00"})
    async with SessionLocal() as session:
        corp = await corporations_repo.get_by_eve_id(session, CORP_ID)
        await lots_repo.create_lot(
            session, corporation_id=corp.id, item_type_id=TRIT, qty=1000,
            unit_purchase_cost=Decimal("3.60"), acquired_at=NOW,
            source="buyback", location_id=JITA,
        )
        await session.commit()
    esi.hangar = {TRIT: 400}

    result = await _run(esi)
    assert result.lots_added == 0 and result.flagged == 1
    lots, events = await _state()
    assert len(lots) == 1 and lots[0].qty_remaining == 1000  # nothing invented
    assert (events[0].kind, events[0].qty, events[0].flagged) == (
        "shortfall", 600, True,
    )

    # Unchanged shortfall → no second event; a CHANGED one logs again.
    await _run(esi)
    _, events = await _state()
    assert len(events) == 1
    esi.hangar = {TRIT: 300}
    await _run(esi)
    _, events = await _state()
    assert len(events) == 2
    assert events[0].qty == 700  # newest first


# --- API -----------------------------------------------------------------------------


async def test_reconciliation_log_lists_enriched_events():
    esi = AssetsEsi()
    await _seed(esi, prices={TRIT: "4.00"})
    esi.hangar = {TRIT: 1000}
    await _run(esi)

    app.dependency_overrides.clear()
    async with make_client(CeoEsi()) as http:
        await login(http)
        resp = await http.get("/api/v1/corporations/me/accounting/reconciliation")
    assert resp.status_code == 200
    (entry,) = resp.json()
    assert entry["kind"] == "excess"
    assert entry["type_name"] == "Tritanium"
    assert entry["location_name"] == "Jita IV - Moon 4"
    assert entry["qty"] == 1000
    assert entry["booked"] is True and entry["flagged"] is False


async def test_manual_check_runs_and_returns_counts():
    esi = AssetsEsi()
    await _seed(esi, prices={TRIT: "4.00"})
    esi.hangar = {TRIT: 1000}

    app.dependency_overrides.clear()
    async with make_client(esi) as http:  # the fake serves connect AND assets
        await login(http)
        resp = await http.post("/api/v1/corporations/me/accounting/hangar-check")
    assert resp.status_code == 200
    assert resp.json() == {"lots_added": 1, "flagged": 0}


async def test_manual_check_maps_missing_scope_to_reconnect_answer():
    esi = AssetsEsi()
    await _seed(esi)
    esi.forbid = True

    app.dependency_overrides.clear()
    async with make_client(esi) as http:
        await login(http)
        resp = await http.post("/api/v1/corporations/me/accounting/hangar-check")
    assert resp.status_code == 409
    assert "reconnect corp esi access" in resp.json()["detail"].lower()
