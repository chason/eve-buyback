"""ADR-0042: payment reconciliation — the journal→entitlement pipeline, its
idempotency, manual matching, the checkout view, and the admin API surface."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from app.application import payments as payments_app
from app.application.errors import PaymentAlreadyMatched, PaymentTooSmall
from app.config import Settings, get_settings
from app.data.db import SessionLocal
from app.data.repositories import corporations as corporations_repo
from app.data.repositories import entitlements as entitlements_repo
from app.data.repositories import operator_wallet as wallet_repo
from app.interface import security
from app.main import app
from app.plugins.esi import WalletJournalEntry, get_esi_client
from app.plugins.sso import OAuthToken, VerifiedCharacter, get_sso_client
from app.plugins.token_cipher import TokenCipher

OPERATOR_CHAR = 90000090
CORP_EVE_ID = 98000001
PRICE = get_settings().accounting_price_isk  # 250M default
NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)


class FakeSso:
    configured = True

    def build_authorize_url(self, *, state, code_challenge, scopes=None):
        return f"https://login.eveonline.com/v2/oauth/authorize?state={state}"

    async def exchange_code(self, code, code_verifier):
        return OAuthToken(access_token="fake-access", refresh_token="fake-refresh")

    async def refresh_access_token(self, refresh_token):
        return OAuthToken(access_token="fresh-access", refresh_token=refresh_token)

    async def verify_token(self, access_token):
        return VerifiedCharacter(character_id=OPERATOR_CHAR, name="Site Operator")

    async def revoke_refresh_token(self, refresh_token):
        pass


def _entry(jid: int, amount: int, reason: str | None, *, ref_type="player_donation",
           to=OPERATOR_CHAR) -> WalletJournalEntry:
    return WalletJournalEntry(
        id=jid,
        ref_type=ref_type,
        amount=Decimal(amount),
        first_party_id=91000001,
        second_party_id=to,
        reason=reason,
        date=NOW,
    )


class FakeEsi:
    def __init__(self, journal: list[WalletJournalEntry]) -> None:
        self.journal = journal

    async def get_character_wallet_journal(self, character_id, access_token):
        return self.journal

    async def resolve_universe_names(self, ids, *, categories=("character",)):
        return {91000001: "Rich Buyer"}


def _cipher() -> TokenCipher:
    return TokenCipher(get_settings().token_encryption_key)


async def _seed(session) -> None:
    await corporations_repo.create_corporation(
        session,
        eve_corporation_id=CORP_EVE_ID,
        name="Test Corp",
        ceo_character_id=1,
        registered_by_character_id=1,
    )
    await wallet_repo.replace(
        session,
        character_eve_id=OPERATOR_CHAR,
        character_name="Site Operator",
        encrypted_refresh_token=_cipher().encrypt("fake-refresh"),
        scopes="esi-wallet.read_character_wallet.v1",
    )
    await session.commit()


async def _corp_uuid(session):
    corp = await corporations_repo.get_by_eve_id(session, CORP_EVE_ID)
    return corp.id


# --- reconciliation ---------------------------------------------------------------


async def test_matching_payment_extends_the_right_corp():
    journal = [
        _entry(1, 2 * PRICE, f"here you go BB-{CORP_EVE_ID}"),  # 2 periods
        _entry(2, PRICE, "no reference at all"),  # unmatched
        _entry(3, PRICE // 10, f"BB-{CORP_EVE_ID}"),  # referenced but too small
        _entry(4, PRICE, "BB-98999999"),  # unknown corp
        _entry(5, 5 * PRICE, "market stuff", ref_type="market_transaction"),  # ignored
    ]
    async with SessionLocal() as session:
        await _seed(session)
        recorded = await payments_app.reconcile_payments(
            session, FakeSso(), FakeEsi(journal), cipher=_cipher(), now=NOW
        )
        assert recorded == 4  # everything but the market entry is recorded

        ent = await entitlements_repo.get(
            session, corporation_id=await _corp_uuid(session), feature="accounting"
        )
        assert ent is not None
        assert ent.source == "payment"
        assert ent.expires_at == NOW + timedelta(days=60)  # 2 periods × 30d

        unmatched = await payments_app.list_payments(session, unmatched_only=True)
        assert {p.journal_id for p in unmatched} == {2, 3, 4}
        matched = [p for p in await payments_app.list_payments(session) if p.matched_at]
        assert [p.journal_id for p in matched] == [1]
        assert matched[0].sender_name == "Rich Buyer"


async def test_reconcile_is_idempotent_across_polls():
    journal = [_entry(1, PRICE, f"BB-{CORP_EVE_ID}")]
    async with SessionLocal() as session:
        await _seed(session)
        assert await payments_app.reconcile_payments(
            session, FakeSso(), FakeEsi(journal), cipher=_cipher(), now=NOW
        ) == 1
        # The same journal page comes back next poll — nothing double-counts.
        assert await payments_app.reconcile_payments(
            session, FakeSso(), FakeEsi(journal), cipher=_cipher(), now=NOW
        ) == 0
        ent = await entitlements_repo.get(
            session, corporation_id=await _corp_uuid(session), feature="accounting"
        )
        assert ent.expires_at == NOW + timedelta(days=30)  # extended exactly once


async def test_payment_stacks_on_active_and_leaves_perpetual_alone():
    async with SessionLocal() as session:
        await _seed(session)
        corp_id = await _corp_uuid(session)
        # An active dated grant stacks.
        await entitlements_repo.upsert(
            session, corporation_id=corp_id, feature="accounting",
            source="admin", expires_at=NOW + timedelta(days=10),
        )
        await payments_app.reconcile_payments(
            session, FakeSso(), FakeEsi([_entry(1, PRICE, f"BB-{CORP_EVE_ID}")]),
            cipher=_cipher(), now=NOW,
        )
        ent = await entitlements_repo.get(session, corporation_id=corp_id, feature="accounting")
        assert ent.expires_at == NOW + timedelta(days=40)
        assert ent.source == "payment"

        # A perpetual grant is left untouched (nothing to extend).
        await entitlements_repo.upsert(
            session, corporation_id=corp_id, feature="accounting",
            source="admin", expires_at=None,
        )
        await payments_app.reconcile_payments(
            session, FakeSso(), FakeEsi([_entry(2, PRICE, f"BB-{CORP_EVE_ID}")]),
            cipher=_cipher(), now=NOW,
        )
        ent = await entitlements_repo.get(session, corporation_id=corp_id, feature="accounting")
        assert ent.expires_at is None
        assert ent.source == "admin"


async def test_no_wallet_connected_is_a_quiet_noop():
    async with SessionLocal() as session:
        assert await payments_app.reconcile_payments(
            session, FakeSso(), FakeEsi([]), cipher=_cipher(), now=NOW
        ) == 0


# --- manual matching --------------------------------------------------------------


async def test_manual_match_applies_and_refuses_repeats_and_underpayments():
    journal = [
        _entry(1, PRICE, "forgot the reference"),
        _entry(2, PRICE // 10, "also forgot, and too small"),
    ]
    async with SessionLocal() as session:
        await _seed(session)
        await payments_app.reconcile_payments(
            session, FakeSso(), FakeEsi(journal), cipher=_cipher(), now=NOW
        )
        unmatched = await payments_app.list_payments(session, unmatched_only=True)
        by_jid = {p.journal_id: p for p in unmatched}

        record = await payments_app.match_payment(
            session, payment_id=by_jid[1].id, corporation_eve_id=CORP_EVE_ID,
            matched_by_character_id=12345, now=NOW,
        )
        assert record.matched_corporation_id is not None
        assert record.periods_granted == 1
        assert record.matched_by_character_id == 12345
        ent = await entitlements_repo.get(
            session, corporation_id=await _corp_uuid(session), feature="accounting"
        )
        assert ent.expires_at == NOW + timedelta(days=30)

        with pytest.raises(PaymentAlreadyMatched):
            await payments_app.match_payment(
                session, payment_id=by_jid[1].id, corporation_eve_id=CORP_EVE_ID,
                matched_by_character_id=12345, now=NOW,
            )
        with pytest.raises(PaymentTooSmall):
            await payments_app.match_payment(
                session, payment_id=by_jid[2].id, corporation_eve_id=CORP_EVE_ID,
                matched_by_character_id=12345, now=NOW,
            )


# --- runtime-editable price (admin-set, ADR-0042) ----------------------------------


async def test_admin_set_price_drives_matching_and_checkout():
    async with SessionLocal() as session:
        await _seed(session)
        # Default comes from the environment until an admin sets a value.
        assert await payments_app.get_price_isk(session) == PRICE

        await payments_app.set_price_isk(session, price_isk=100_000_000)
        assert await payments_app.get_price_isk(session) == 100_000_000

        # A 250M payment now buys 2 whole periods at the 100M price.
        await payments_app.reconcile_payments(
            session, FakeSso(), FakeEsi([_entry(1, 250_000_000, f"BB-{CORP_EVE_ID}")]),
            cipher=_cipher(), now=NOW,
        )
        ent = await entitlements_repo.get(
            session, corporation_id=await _corp_uuid(session), feature="accounting"
        )
        assert ent.expires_at == NOW + timedelta(days=60)

        # Checkout reflects the admin-set price too.
        info = await payments_app.checkout_info(
            session, corporation_eve_id=CORP_EVE_ID, now=NOW
        )
        assert info.price_isk == 100_000_000


# --- checkout ----------------------------------------------------------------------


async def test_checkout_info_shows_reference_and_operator():
    async with SessionLocal() as session:
        await _seed(session)
        info = await payments_app.checkout_info(
            session, corporation_eve_id=CORP_EVE_ID, now=NOW
        )
        assert info.active is False
        assert info.reference == f"BB-{CORP_EVE_ID}"
        assert info.payment_configured is True
        assert info.operator_character_name == "Site Operator"
        assert info.price_isk == PRICE


# --- API surface --------------------------------------------------------------------


class FakeApiEsi:
    """Login-flow ESI fake: the caller is the CEO of the registered corp."""

    async def get_character(self, character_id):
        from app.plugins.esi import CharacterInfo

        return CharacterInfo(name="Boss", corporation_id=CORP_EVE_ID)

    async def get_character_corporation(self, character_id):
        return CORP_EVE_ID

    async def get_corporation(self, corporation_id):
        from app.plugins.esi import CorporationInfo

        return CorporationInfo(name="Test Corp", ceo_id=12345, ticker="T")

    async def get_character_roles(self, character_id, access_token):
        return []


class FakeApiSso(FakeSso):
    async def verify_token(self, access_token):
        return VerifiedCharacter(character_id=12345, name="Boss")


@pytest.fixture
def client(request: pytest.FixtureRequest, monkeypatch):
    admin_ids = getattr(request, "param", "")
    settings = Settings(
        environment="development", admin_character_ids=admin_ids, _env_file=None
    )
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    app.dependency_overrides[get_sso_client] = lambda: FakeApiSso()
    app.dependency_overrides[get_esi_client] = lambda: FakeApiEsi()
    yield AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Buyback-CSRF": "1"},
    )
    app.dependency_overrides.clear()


async def _login(http: AsyncClient) -> None:
    state = (await http.post("/api/v1/auth/login")).json()["state"]
    assert (
        await http.post("/api/v1/auth/session", json={"code": "c", "state": state})
    ).status_code == 200


async def test_wallet_and_payment_endpoints_reject_non_admin(client):
    async with client as http:
        await _login(http)
        assert (await http.get("/api/v1/admin/wallet")).status_code == 403
        assert (await http.get("/api/v1/admin/payments")).status_code == 403
        assert (await http.get("/api/v1/admin/billing")).status_code == 403
        assert (
            await http.put("/api/v1/admin/billing", json={"price_isk": 1})
        ).status_code == 403


@pytest.mark.parametrize("client", ["12345"], indirect=True)
async def test_admin_edits_the_price(client):
    async with client as http:
        await _login(http)
        before = (await http.get("/api/v1/admin/billing")).json()
        assert before["price_isk"] == PRICE

        updated = await http.put(
            "/api/v1/admin/billing", json={"price_isk": 500_000_000}
        )
        assert updated.status_code == 200
        assert updated.json()["price_isk"] == 500_000_000
        assert (await http.get("/api/v1/admin/billing")).json()["price_isk"] == 500_000_000

        # Zero/negative prices are refused at the contract.
        assert (
            await http.put("/api/v1/admin/billing", json={"price_isk": 0})
        ).status_code == 422


@pytest.mark.parametrize("client", ["12345"], indirect=True)
async def test_admin_sees_wallet_status_and_matches_a_payment(client):
    async with SessionLocal() as session:
        await _seed(session)
        await payments_app.reconcile_payments(
            session, FakeSso(), FakeEsi([_entry(1, PRICE, "no ref")]),
            cipher=_cipher(), now=NOW,
        )
    async with client as http:
        await _login(http)
        wallet = (await http.get("/api/v1/admin/wallet")).json()
        assert wallet["connected"] is True
        assert wallet["character_name"] == "Site Operator"

        unmatched = (await http.get("/api/v1/admin/payments?unmatched=true")).json()
        assert len(unmatched) == 1
        assert unmatched[0]["matched"] is False

        matched = await http.post(
            f"/api/v1/admin/payments/{unmatched[0]['id']}/match",
            json={"corporation_id": CORP_EVE_ID},
        )
        assert matched.status_code == 200
        assert matched.json()["matched"] is True
        assert matched.json()["matched_corporation_name"] == "Test Corp"


async def test_manager_checkout_endpoint(client):
    async with SessionLocal() as session:
        await _seed(session)
    async with client as http:
        await _login(http)  # CEO of the registered corp → manager-gated read passes
        resp = await http.get("/api/v1/corporations/me/accounting-access")
        assert resp.status_code == 200
        body = resp.json()
        assert body["reference"] == f"BB-{CORP_EVE_ID}"
        assert body["payment_configured"] is True
        assert body["active"] is False