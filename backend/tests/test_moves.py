"""#200 / ADR-0049: move detection — the pure pairing function (same type only,
single unambiguous pair, min() cap), the sync wiring (suggest-only: the shortfall
flag + deemed-cost lot are still booked exactly as before), pending-pair
idempotency across syncs, and the entitlement-gated list use case + API."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.application import corp_esi_token as corp_esi_token_app
from app.application import reconciliation as recon_app
from app.application.auth import AuthenticatedUser
from app.application.errors import EntitlementRequired
from app.data.db import SessionLocal
from app.data.models import MarketPrice
from app.data.repositories import buyback_config as config_repo
from app.data.repositories import corporations as corporations_repo
from app.data.repositories import entitlements as entitlements_repo
from app.data.repositories import hangars as hangars_repo
from app.data.repositories import lots as lots_repo
from app.data.repositories import move_suggestions as moves_repo
from app.data.repositories import reconciliation as recon_repo
from app.data.repositories import sde as sde_repo
from app.domain.moves import MovePair, match_move_pairs
from app.main import app
from app.plugins.esi import (
    CharacterInfo,
    CorporationAsset,
    CorporationInfo,
)
from app.plugins.sso import OAuthToken, VerifiedCharacter
from app.plugins.token_cipher import get_token_cipher
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


# --- the pure pairing function -----------------------------------------------------


def test_pairs_same_type_across_hangars_capped_at_smaller_side():
    pairs = match_move_pairs(
        shortfalls=[(JITA, TRIT, 600)], excesses=[(AMARR, TRIT, 350)]
    )
    assert pairs == [
        MovePair(
            type_id=TRIT,
            origin_location_id=JITA,
            destination_location_id=AMARR,
            qty=350,
        )
    ]


def test_never_pairs_across_types_even_on_a_quantity_match():
    # 600 Tritanium missing, 600 Pyerite found — numerology, not a move.
    assert match_move_pairs([(JITA, TRIT, 600)], [(AMARR, PYE, 600)]) == []


def test_ambiguous_candidates_pair_nothing_but_unambiguous_types_still_do():
    # Tritanium short at TWO stations with one excess is ambiguous (#203 lists
    # candidates); Pyerite's single pair still proposes.
    pairs = match_move_pairs(
        shortfalls=[(JITA, TRIT, 100), ("60011866", TRIT, 50), (JITA, PYE, 20)],
        excesses=[(AMARR, TRIT, 150), (AMARR, PYE, 80)],
    )
    assert pairs == [
        MovePair(
            type_id=PYE,
            origin_location_id=JITA,
            destination_location_id=AMARR,
            qty=20,
        )
    ]


def test_no_pairs_without_both_sides():
    assert match_move_pairs([(JITA, TRIT, 100)], []) == []
    assert match_move_pairs([], [(AMARR, TRIT, 100)]) == []


# --- sync wiring harness (two marked hangars) --------------------------------------


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


class MultiHangarEsi:
    """ESI fake: token-connect validation + an assets read spanning multiple
    stations. `hangar` is `(location_id, type_id) → qty` in CorpSAG2."""

    def __init__(self):
        self.hangar: dict[tuple[str, int], int] = {}
        self._item_id = iter(range(7_000_000, 8_000_000))

    async def get_character(self, character_id):
        return CharacterInfo(name="Boss", corporation_id=CORP_ID)

    async def get_character_corporation(self, character_id):
        return CORP_ID

    async def get_corporation(self, corporation_id):
        return CorporationInfo(name="Test Corp", ceo_id=CHAR_ID, ticker="T")

    async def get_character_roles(self, character_id, access_token):
        return []

    async def get_corporation_assets(self, corporation_id, access_token):
        assets = []
        for (loc, tid), qty in self.hangar.items():
            assets.append(
                CorporationAsset(
                    item_id=next(self._item_id), type_id=tid, quantity=qty,
                    location_id=int(loc), location_flag="CorpSAG2",
                )
            )
        return assets


def _user() -> AuthenticatedUser:
    return AuthenticatedUser(
        character_id=CHAR_ID, character_name="Boss", corporation_id=CORP_ID,
        corporation_name="Test Corp", role="ceo", is_director=False,
        corporation_registered=True,
    )


async def _seed(
    esi: MultiHangarEsi,
    *,
    prices: dict[int, str] | None = None,
    entitled: bool = True,
) -> None:
    """Registered corp with marked hangars at Jita AND Amarr (division 2), Jita
    config, SDE types, cached prices, and a connected corp ESI token."""
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
        for type_id, buy in (prices or {}).items():
            _add_price(session, type_id, Decimal(buy))
        await session.commit()
    async with SessionLocal() as session:
        await corp_esi_token_app.complete_corp_esi_authorize(
            session, FakeSso(), esi, code="c", verifier="v",
            user=_user(), cipher=get_token_cipher(),
        )


def _add_price(session, type_id: int, b: Decimal) -> None:
    session.add(MarketPrice(
        hub_id=JITA, type_id=type_id,
        buy_weighted_average=b, buy_max=b, buy_min=b, buy_median=b,
        buy_percentile=b, buy_volume=Decimal(1000), buy_order_count=10,
        sell_weighted_average=b, sell_max=b, sell_min=b, sell_median=b,
        sell_percentile=b, sell_volume=Decimal(1000), sell_order_count=10,
        fetched_at=NOW,
    ))


async def _book_lot(type_id: int, qty: int, location_id: str) -> None:
    async with SessionLocal() as session:
        corp = await corporations_repo.get_by_eve_id(session, CORP_ID)
        await lots_repo.create_lot(
            session, corporation_id=corp.id, item_type_id=type_id, qty=qty,
            unit_purchase_cost=Decimal("5.25"), acquired_at=NOW,
            source="buyback", location_id=location_id,
        )
        await session.commit()


async def _run(esi: MultiHangarEsi):
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
        suggestions = await moves_repo.list_pending(session, corporation_id=corp.id)
    return lots, events, suggestions


# --- the sync proposes, the defaults still book (the suggest-only invariant) -------


async def test_sync_suggests_a_move_and_still_books_the_defaults():
    esi = MultiHangarEsi()
    await _seed(esi, prices={TRIT: "4.00"})
    await _book_lot(TRIT, 1000, JITA)
    # 600 gone from Jita; 350 of the same type surfaced at Amarr.
    esi.hangar = {(JITA, TRIT): 400, (AMARR, TRIT): 350}

    result = await _run(esi)

    # The ADR-0044 defaults are booked exactly as before — the suggestion never
    # blocks or replaces them.
    assert result.lots_added == 1 and result.flagged == 1
    lots, events, suggestions = await _state()
    deemed = next(lot for lot in lots if lot.location_id == AMARR)
    assert deemed.qty_remaining == 350 and deemed.cost_is_estimated is True
    shortfall = next(e for e in events if e.kind == "shortfall")
    assert (shortfall.qty, shortfall.flagged) == (600, True)

    # The decoration: one pending pair, capped at the smaller side, keyed to the
    # flag and the deemed lot it decorates.
    (s,) = suggestions
    assert (s.type_id, s.qty, s.status) == (TRIT, 350, "pending")
    assert s.origin_location_id == JITA
    assert s.destination_location_id == AMARR
    assert s.shortfall_event_id == shortfall.id
    assert s.excess_lot_id == deemed.id

    # A later sync seeing the same world must not duplicate the pending pair.
    again = await _run(esi)
    assert again.lots_added == 0 and again.flagged == 0
    _, _, suggestions = await _state()
    assert len(suggestions) == 1


async def test_unpriceable_excess_pairs_once_the_lot_books():
    esi = MultiHangarEsi()
    await _seed(esi)  # no cached prices at all
    await _book_lot(TRIT, 1000, JITA)
    esi.hangar = {(JITA, TRIT): 400, (AMARR, TRIT): 600}

    await _run(esi)
    _, events, suggestions = await _state()
    # No lot yet → nothing to decorate; both defaults are flagged as usual.
    assert suggestions == []
    assert {e.kind for e in events} == {"shortfall", "excess"}

    # A price appears → the next pass books the deemed lot AND proposes the
    # pair against the shortfall flag logged earlier.
    async with SessionLocal() as session:
        _add_price(session, TRIT, Decimal("4.00"))
        await session.commit()
    await _run(esi)
    _, events, suggestions = await _state()
    (s,) = suggestions
    shortfall = next(e for e in events if e.kind == "shortfall")
    assert s.shortfall_event_id == shortfall.id
    assert s.qty == 600


# --- the list use case + API -------------------------------------------------------


async def test_list_move_suggestions_requires_the_accounting_entitlement():
    esi = MultiHangarEsi()
    await _seed(esi, entitled=False)
    async with SessionLocal() as session:
        with pytest.raises(EntitlementRequired):
            await recon_app.list_move_suggestions(
                session, corporation_eve_id=CORP_ID
            )


async def test_move_suggestions_endpoint_returns_plain_named_cards():
    esi = MultiHangarEsi()
    await _seed(esi, prices={TRIT: "4.00"})
    await _book_lot(TRIT, 1000, JITA)
    esi.hangar = {(JITA, TRIT): 400, (AMARR, TRIT): 350}
    await _run(esi)

    app.dependency_overrides.clear()
    async with make_client(CeoEsi()) as http:
        await login(http)
        resp = await http.get(
            "/api/v1/corporations/me/accounting/move-suggestions"
        )
    assert resp.status_code == 200
    (card,) = resp.json()
    assert card["type_name"] == "Tritanium"
    assert card["origin_name"] == "Jita IV - Moon 4"
    assert card["destination_name"] == "Amarr VIII - Emperor Family Academy"
    assert card["qty"] == 350
