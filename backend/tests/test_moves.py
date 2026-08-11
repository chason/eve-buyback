"""#200 / ADR-0049: move detection — the pure pairing function (same type only,
single unambiguous pair, min() cap), the sync wiring (suggest-only: the shortfall
flag + deemed-cost lot are still booked exactly as before), pending-pair
idempotency across syncs, and the entitlement-gated list use case + API.

#202: the two non-confirm exits — dismiss ("not a move": defaults stand, logged
with the acting character, the unchanged pattern never re-asked) and
auto-withdraw (a later sync invalidates either end of the pair)."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.application import corp_esi_token as corp_esi_token_app
from app.application import reconciliation as recon_app
from app.application.auth import AuthenticatedUser
from app.application.errors import (
    EntitlementRequired,
    MoveSuggestionNotFound,
    MoveSuggestionNotPending,
)
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
from app.plugins.sso import OAuthToken, VerifiedCharacter
from app.plugins.token_cipher import get_token_cipher
from tests.helpers import CHAR_ID, CORP_ID, CeoEsi, HangarAssetsEsi, login, make_client

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
            qty_counted=350,
        )
    ]


def test_never_pairs_across_types_even_on_a_quantity_match():
    # 600 Tritanium missing, 600 Pyerite found — numerology, not a move.
    assert match_move_pairs([(JITA, TRIT, 600)], [(AMARR, PYE, 600)]) == []


def test_two_candidate_origins_for_one_excess_both_propose():
    # Tritanium short at TWO stations with one excess (#203): one candidate
    # pair per origin, each capped by its own shortfall, sharing the excess.
    # Pyerite's single unambiguous pair proposes as before (#200).
    pairs = match_move_pairs(
        shortfalls=[(JITA, TRIT, 100), ("60011866", TRIT, 50), (JITA, PYE, 20)],
        excesses=[(AMARR, TRIT, 150), (AMARR, PYE, 80)],
    )
    assert pairs == [
        MovePair(
            type_id=TRIT,
            origin_location_id=JITA,
            destination_location_id=AMARR,
            qty=100,
            qty_counted=100,
        ),
        MovePair(
            type_id=TRIT,
            origin_location_id="60011866",
            destination_location_id=AMARR,
            qty=50,
            qty_counted=50,
        ),
        MovePair(
            type_id=PYE,
            origin_location_id=JITA,
            destination_location_id=AMARR,
            qty=20,
            qty_counted=20,
        ),
    ]


def test_an_ambiguous_destination_still_proposes_nothing():
    # One shortfall, TWO excess locations: with the destination itself in doubt
    # there is no single found-stock lot for candidates to share (#203 scopes
    # the manager-picks flow to candidate ORIGINS) — the defaults stand.
    pairs = match_move_pairs(
        shortfalls=[(JITA, TRIT, 100)],
        excesses=[(AMARR, TRIT, 60), ("60011866", TRIT, 40)],
    )
    assert pairs == []


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


def _user() -> AuthenticatedUser:
    return AuthenticatedUser(
        character_id=CHAR_ID, character_name="Boss", corporation_id=CORP_ID,
        corporation_name="Test Corp", role="ceo", is_director=False,
        corporation_registered=True,
    )


async def _seed(
    esi: HangarAssetsEsi,
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


async def _run(esi: HangarAssetsEsi):
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
    esi = HangarAssetsEsi()
    await _seed(esi, prices={TRIT: "4.00"})
    await _book_lot(TRIT, 1000, JITA)
    # 600 gone from Jita; 350 of the same type surfaced at Amarr.
    esi.stock = {(JITA, TRIT): 400, (AMARR, TRIT): 350}

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
    esi = HangarAssetsEsi()
    await _seed(esi)  # no cached prices at all
    await _book_lot(TRIT, 1000, JITA)
    esi.stock = {(JITA, TRIT): 400, (AMARR, TRIT): 600}

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
    esi = HangarAssetsEsi()
    await _seed(esi, entitled=False)
    async with SessionLocal() as session:
        with pytest.raises(EntitlementRequired):
            await recon_app.list_move_suggestions(
                session, corporation_eve_id=CORP_ID
            )


async def test_move_suggestions_endpoint_returns_plain_named_cards():
    esi = HangarAssetsEsi()
    await _seed(esi, prices={TRIT: "4.00"})
    await _book_lot(TRIT, 1000, JITA)
    esi.stock = {(JITA, TRIT): 400, (AMARR, TRIT): 350}
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


# --- dismiss ("not a move", #202) --------------------------------------------------


async def _dismiss(suggestion_id: uuid.UUID, name: str = "Boss") -> None:
    async with SessionLocal() as session:
        await recon_app.dismiss_move_suggestion(
            session, corporation_eve_id=CORP_ID, suggestion_id=suggestion_id,
            dismissed_by_character_name=name,
        )


async def _suggestion_status(suggestion_id: uuid.UUID) -> str:
    async with SessionLocal() as session:
        corp = await corporations_repo.get_by_eve_id(session, CORP_ID)
        record = await moves_repo.get_for_corp(
            session, corporation_id=corp.id, suggestion_id=suggestion_id
        )
    assert record is not None
    return record.status


async def _suggest_one(esi: HangarAssetsEsi):
    """Seed + book + run one sync that proposes exactly one pair; returns it."""
    await _seed(esi, prices={TRIT: "4.00"})
    await _book_lot(TRIT, 1000, JITA)
    esi.stock = {(JITA, TRIT): 400, (AMARR, TRIT): 350}
    await _run(esi)
    _, _, suggestions = await _state()
    (s,) = suggestions
    return s


async def test_dismiss_resolves_logs_and_leaves_the_defaults_alone():
    esi = HangarAssetsEsi()
    s = await _suggest_one(esi)

    await _dismiss(s.id, name="Boss")

    lots, events, suggestions = await _state()
    # The suggestion is resolved; the booked defaults are exactly as they were
    # (ADR-0044 stands): flag still up, deemed lot still estimated.
    assert suggestions == []
    assert await _suggestion_status(s.id) == "dismissed"
    deemed = next(lot for lot in lots if lot.location_id == AMARR)
    assert deemed.qty_remaining == 350 and deemed.cost_is_estimated is True
    shortfall = next(e for e in events if e.kind == "shortfall")
    assert shortfall.flagged is True and shortfall.id == s.shortfall_event_id
    # The decision is in the log, with who made it.
    dismissed = next(e for e in events if e.kind == "move_dismissed")
    assert (dismissed.location_id, dismissed.type_id, dismissed.qty) == (
        JITA, TRIT, 350,
    )
    assert dismissed.note == "Not a move — dismissed by Boss"

    # An unchanged world doesn't re-ask AND doesn't re-flag: the dismissal
    # annotation must not make the standing shortfall look new again.
    again = await _run(esi)
    assert again.lots_added == 0 and again.flagged == 0
    _, events, suggestions = await _state()
    assert suggestions == []
    assert sum(1 for e in events if e.kind == "shortfall") == 1


async def test_dismissed_pattern_is_not_reasked_but_a_changed_one_is():
    esi = HangarAssetsEsi()
    s = await _suggest_one(esi)
    await _dismiss(s.id)

    # The same pattern re-materializes: another 350 surfaces at Amarr against
    # the SAME standing shortfall flag → same anchor, same qty. The default
    # (deemed lot) still books; the question a human already answered doesn't
    # come back.
    esi.stock = {(JITA, TRIT): 400, (AMARR, TRIT): 700}
    result = await _run(esi)
    assert result.lots_added == 1
    _, _, suggestions = await _state()
    assert suggestions == []

    # A different quantity is a changed pattern — it may suggest again.
    esi.stock = {(JITA, TRIT): 400, (AMARR, TRIT): 900}
    await _run(esi)
    _, _, suggestions = await _state()
    (fresh,) = suggestions
    assert fresh.qty == 200
    assert fresh.shortfall_event_id == s.shortfall_event_id


async def test_dismiss_requires_the_accounting_entitlement():
    esi = HangarAssetsEsi()
    await _seed(esi, entitled=False)
    with pytest.raises(EntitlementRequired):
        await _dismiss(uuid.uuid4())


async def test_dismissing_an_unknown_suggestion_is_not_found():
    esi = HangarAssetsEsi()
    await _seed(esi)
    with pytest.raises(MoveSuggestionNotFound):
        await _dismiss(uuid.uuid4())


async def test_dismiss_is_idempotent_but_conflicts_once_resolved_otherwise():
    esi = HangarAssetsEsi()
    s = await _suggest_one(esi)

    await _dismiss(s.id)
    await _dismiss(s.id)  # a repeat is a no-op, not an error
    _, events, _ = await _state()
    assert sum(1 for e in events if e.kind == "move_dismissed") == 1

    # A suggestion that left `pending` any other way (confirmed by #201, or
    # withdrawn by a sync) has nothing left for a dismissal to decide.
    async with SessionLocal() as session:
        await moves_repo.set_status(
            session, suggestion_id=s.id, status="confirmed"
        )
        await session.commit()
    with pytest.raises(MoveSuggestionNotPending):
        await _dismiss(s.id)


async def test_dismiss_endpoint_removes_the_card():
    esi = HangarAssetsEsi()
    await _suggest_one(esi)

    app.dependency_overrides.clear()
    async with make_client(CeoEsi()) as http:
        await login(http)
        listed = await http.get(
            "/api/v1/corporations/me/accounting/move-suggestions"
        )
        (card,) = listed.json()
        resp = await http.post(
            "/api/v1/corporations/me/accounting/move-suggestions/"
            f"{card['id']}/dismiss"
        )
        assert resp.status_code == 204
        relisted = await http.get(
            "/api/v1/corporations/me/accounting/move-suggestions"
        )
    assert relisted.json() == []


# --- auto-withdraw (#202) ----------------------------------------------------------


async def test_withdraws_when_the_excess_evaporates():
    esi = HangarAssetsEsi()
    s = await _suggest_one(esi)

    # The found stock leaves Amarr before anyone confirmed: the pair no longer
    # holds. The withdrawal is bookkeeping — logged with its own kind, never
    # worded like a human's dismissal — and the new Amarr shortfall gets the
    # normal default treatment.
    esi.stock = {(JITA, TRIT): 400}
    await _run(esi)

    _, events, suggestions = await _state()
    assert suggestions == []
    assert await _suggestion_status(s.id) == "withdrawn"
    withdrawn = next(e for e in events if e.kind == "move_withdrawn")
    assert (withdrawn.location_id, withdrawn.qty) == (JITA, 350)
    assert withdrawn.note == "The found stock is no longer there"
    shortfalls = [e for e in events if e.kind == "shortfall"]
    assert {(e.location_id, e.qty) for e in shortfalls} == {
        (JITA, 600), (AMARR, 350),
    }


async def test_withdraws_when_the_shortfall_resolves_some_other_way():
    esi = HangarAssetsEsi()
    s = await _suggest_one(esi)

    # The missing stock turns up back at Jita — the hangar matches the books
    # again (no deltas at all), so the flag the pair decorated is moot.
    esi.stock = {(JITA, TRIT): 1000, (AMARR, TRIT): 350}
    result = await _run(esi)
    assert result.lots_added == 0 and result.flagged == 0

    _, events, suggestions = await _state()
    assert suggestions == []
    assert await _suggestion_status(s.id) == "withdrawn"
    withdrawn = next(e for e in events if e.kind == "move_withdrawn")
    assert withdrawn.note == "The missing-stock flag was resolved another way"
