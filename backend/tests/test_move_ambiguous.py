"""#203 / ADR-0049: cross-sync and ambiguous pairing.

Cross-sync: a shortfall flagged in sync N pairs with an excess that only
appears in sync N+1 — the freighter may be mid-haul when a sync runs; the
standing flag anchors the later pair.

Ambiguity: the same type short at TWO stations with one excess proposes one
candidate suggestion per origin, all sharing the found-stock lot (`group_id`
in the API). The manager picks by confirming ONE candidate — nothing is
auto-chosen; residuals keep the ADR-0044 defaults. A confirm that fully
claims the lot withdraws the sibling candidates (bookkeeping, #202's stance);
a partial claim leaves them pending at their shrunken convertible quantity."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.application import corp_esi_token as corp_esi_token_app
from app.application import moves as moves_app
from app.application import reconciliation as recon_app
from app.application.auth import AuthenticatedUser
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
from app.main import app
from app.plugins.esi import CharacterInfo, CorporationAsset, CorporationInfo
from app.plugins.sso import OAuthToken, VerifiedCharacter
from app.plugins.token_cipher import get_token_cipher
from tests.helpers import CHAR_ID, CORP_ID, CeoEsi, login, make_client

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
JITA = "60003760"
AMARR = "60008494"
DODIXIE = "60011866"
TRIT = 34


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


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


async def _seed(esi: MultiHangarEsi) -> None:
    """Registered, entitled corp with THREE marked hangars (Jita, Amarr,
    Dodixie), a cached Tritanium price, and a connected corp ESI token."""
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
        await hangars_repo.add(
            session, corporation_id=corp.id, location_id=JITA,
            location_name="Jita IV - Moon 4", division=2,
        )
        await hangars_repo.add(
            session, corporation_id=corp.id, location_id=AMARR,
            location_name="Amarr VIII - Emperor Family Academy", division=2,
        )
        await hangars_repo.add(
            session, corporation_id=corp.id, location_id=DODIXIE,
            location_name="Dodixie IX - Moon 20", division=2,
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
    async with SessionLocal() as session:
        await corp_esi_token_app.complete_corp_esi_authorize(
            session, FakeSso(), esi, code="c", verifier="v",
            user=_user(), cipher=get_token_cipher(),
        )


async def _book_lot(qty: int, location_id: str) -> None:
    async with SessionLocal() as session:
        corp = await corporations_repo.get_by_eve_id(session, CORP_ID)
        await lots_repo.create_lot(
            session, corporation_id=corp.id, item_type_id=TRIT, qty=qty,
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
        pending = await moves_repo.list_pending(session, corporation_id=corp.id)
    return lots, events, pending


async def _confirm(suggestion_id) -> int:
    async with SessionLocal() as session:
        result = await moves_app.confirm_move(
            session, corporation_eve_id=CORP_ID, suggestion_id=suggestion_id,
            confirmed_by_character_id=CHAR_ID, confirmed_by_name="Boss",
            now=NOW,
        )
    return result.qty_moved


async def _views():
    async with SessionLocal() as session:
        return await recon_app.list_move_suggestions(
            session, corporation_eve_id=CORP_ID
        )


def _by_origin(suggestions, origin: str):
    return next(s for s in suggestions if s.origin_location_id == origin)


# --- cross-sync: the standing flag anchors a later pair (mid-haul) -----------------


async def test_mid_haul_shortfall_pairs_with_a_later_syncs_excess():
    esi = MultiHangarEsi()
    await _seed(esi)
    await _book_lot(1000, JITA)

    # Sync N: the freighter is in space — 600 gone from Jita, nothing has
    # arrived anywhere. The shortfall flags; no pair is possible yet.
    esi.hangar = {(JITA, TRIT): 400}
    await _run(esi)
    _, events, pending = await _state()
    assert pending == []
    (flag,) = [e for e in events if e.kind == "shortfall"]
    assert (flag.location_id, flag.qty, flag.flagged) == (JITA, 600, True)

    # Sync N+1: the freighter docked — 350 surfaced at Amarr. The pair
    # proposes against the STANDING flag from sync N: no new shortfall event,
    # the old one anchors.
    esi.hangar = {(JITA, TRIT): 400, (AMARR, TRIT): 350}
    await _run(esi)
    _, events, pending = await _state()
    assert sum(1 for e in events if e.kind == "shortfall") == 1
    (s,) = pending
    assert (s.origin_location_id, s.destination_location_id) == (JITA, AMARR)
    assert s.qty == 350
    assert s.shortfall_event_id == flag.id


# --- ambiguity: candidate origins, listed for the manager to pick ------------------


async def _suggest_candidates(
    esi: MultiHangarEsi, *, jita_short: int, dodixie_short: int, excess: int
):
    """One sync where Tritanium is short at Jita AND Dodixie and over at
    Amarr; returns the two pending candidate suggestions."""
    await _seed(esi)
    await _book_lot(1000, JITA)
    await _book_lot(500, DODIXIE)
    esi.hangar = {
        (JITA, TRIT): 1000 - jita_short,
        (DODIXIE, TRIT): 500 - dodixie_short,
        (AMARR, TRIT): excess,
    }
    await _run(esi)
    _, _, pending = await _state()
    assert len(pending) == 2
    return _by_origin(pending, JITA), _by_origin(pending, DODIXIE)


async def test_two_candidate_origins_share_the_lot_each_with_its_own_anchor():
    esi = MultiHangarEsi()
    from_jita, from_dodixie = await _suggest_candidates(
        esi, jita_short=100, dodixie_short=50, excess=150
    )

    # Each candidate is a full pair: its own shortfall anchor and quantity cap,
    # both decorating the SAME deemed lot (the group the UI folds together).
    assert from_jita.qty == 100 and from_dodixie.qty == 50
    assert from_jita.excess_lot_id == from_dodixie.excess_lot_id
    assert from_jita.shortfall_event_id != from_dodixie.shortfall_event_id
    lots, _, _ = await _state()
    deemed = next(lot for lot in lots if lot.location_id == AMARR)
    assert from_jita.excess_lot_id == deemed.id

    # An unchanged world re-syncs without duplicating either candidate.
    await _run(esi)
    _, _, pending = await _state()
    assert len(pending) == 2


async def test_candidate_cards_expose_a_shared_group_id():
    esi = MultiHangarEsi()
    await _suggest_candidates(esi, jita_short=100, dodixie_short=50, excess=150)

    app.dependency_overrides.clear()
    async with make_client(CeoEsi()) as http:
        await login(http)
        resp = await http.get(
            "/api/v1/corporations/me/accounting/move-suggestions"
        )
    assert resp.status_code == 200
    cards = resp.json()
    assert len(cards) == 2
    assert cards[0]["group_id"] == cards[1]["group_id"]
    assert cards[0]["group_id"] is not None
    assert {c["origin_name"] for c in cards} == {
        "Jita IV - Moon 4", "Dodixie IX - Moon 20",
    }


# --- confirm-with-choice -----------------------------------------------------------


async def test_confirming_one_candidate_converts_it_and_withdraws_the_claimed_sibling():
    esi = MultiHangarEsi()
    from_jita, from_dodixie = await _suggest_candidates(
        esi, jita_short=350, dodixie_short=350, excess=350
    )
    assert from_jita.qty == 350 and from_dodixie.qty == 350

    # The manager says it came from Dodixie. Only that candidate converts —
    # nothing was auto-chosen.
    assert await _confirm(from_dodixie.id) == 350

    lots, events, pending = await _state()
    # The stock relocated FROM DODIXIE, cost basis intact; the deemed lot is
    # fully reversed.
    moved = [lot for lot in lots if lot.location_id == AMARR]
    assert [lot.unit_purchase_cost for lot in moved] == [Decimal("5.25")]
    assert not any(lot.cost_is_estimated for lot in moved)
    # Jita's residual keeps the defaults: its shortfall flag still stands,
    # untouched by the other origin's confirm.
    jita_flag = next(
        e for e in events if e.kind == "shortfall" and e.location_id == JITA
    )
    assert jita_flag.flagged is True
    # The claimed sibling is withdrawn — bookkeeping, worded as a claim, never
    # as a human's "not a move".
    assert pending == []
    async with SessionLocal() as session:
        corp = await corporations_repo.get_by_eve_id(session, CORP_ID)
        sibling = await moves_repo.get_for_corp(
            session, corporation_id=corp.id, suggestion_id=from_jita.id
        )
    assert sibling is not None and sibling.status == "withdrawn"
    withdrawn = next(e for e in events if e.kind == "move_withdrawn")
    assert (withdrawn.location_id, withdrawn.qty) == (JITA, 350)
    assert withdrawn.note == (
        "The found stock was confirmed as a move from Dodixie IX - Moon 20"
    )


async def test_a_partial_claim_leaves_the_sibling_pending_at_what_remains():
    esi = MultiHangarEsi()
    from_jita, from_dodixie = await _suggest_candidates(
        esi, jita_short=100, dodixie_short=350, excess=350
    )
    assert from_jita.qty == 100 and from_dodixie.qty == 350

    # Confirming the smaller candidate claims only part of the found stock: a
    # haul can arrive from two places, so the other candidate stays live —
    # its card just shrinks to the unclaimed remainder (#204's cap).
    assert await _confirm(from_jita.id) == 100

    _, events, pending = await _state()
    (still_pending,) = pending
    assert still_pending.id == from_dodixie.id
    assert not any(e.kind == "move_withdrawn" for e in events)
    (view,) = await _views()
    assert view.record.id == from_dodixie.id
    assert view.convertible_qty == 250


async def test_dismissing_one_candidate_leaves_the_other_actionable():
    esi = MultiHangarEsi()
    from_jita, from_dodixie = await _suggest_candidates(
        esi, jita_short=100, dodixie_short=50, excess=150
    )

    # "It didn't come from Jita" — a per-candidate decision (#202): only that
    # origin's question is answered; the other candidate stays.
    async with SessionLocal() as session:
        await recon_app.dismiss_move_suggestion(
            session, corporation_eve_id=CORP_ID, suggestion_id=from_jita.id,
            dismissed_by_character_name="Boss", now=NOW,
        )
    _, _, pending = await _state()
    assert [s.id for s in pending] == [from_dodixie.id]

    # An unchanged world neither re-asks the dismissed candidate nor
    # duplicates the pending one.
    await _run(esi)
    _, _, pending = await _state()
    assert [s.id for s in pending] == [from_dodixie.id]

    # The surviving candidate is still fully actionable.
    assert await _confirm(from_dodixie.id) == 50
