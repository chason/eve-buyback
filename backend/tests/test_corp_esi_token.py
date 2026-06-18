"""Corp ESI access token infrastructure (ADR-0029, ADR-0036): cipher, the
CEO/Director-gated connect flow, and server-side refresh (rotation + expiry)."""

import uuid

import httpx
import pytest
from cryptography.fernet import Fernet, InvalidToken

from app.application import corp_esi_token as corp_esi_token_app
from app.application.auth import AuthenticatedUser
from app.application.errors import (
    AuthorizingCharacterNotInCorporation,
    CorpEsiTokenExpired,
    CorpEsiTokenMissing,
    NotAuthorizedToAuthorizeStructure,
    StructureEncryptionNotConfigured,
)
from app.config import get_settings
from app.data.db import SessionLocal
from app.data.repositories import corp_esi_token as tokens_repo
from app.data.repositories import corporations as corporations_repo
from app.domain.roles import Role
from app.plugins.sso import OAuthToken, VerifiedCharacter
from app.plugins.token_cipher import TokenCipher
from tests.helpers import CeoEsi, MemberEsi, login, make_client

CORP_EVE_ID = 98000001
CHAR_ID = 12345
CIPHER = TokenCipher(Fernet.generate_key().decode())


def _user(role: Role, *, is_director: bool = False) -> AuthenticatedUser:
    return AuthenticatedUser(
        character_id=CHAR_ID,
        character_name="Boss",
        corporation_id=CORP_EVE_ID,
        corporation_name="Test Corp",
        role=role,
        is_director=is_director,
        corporation_registered=True,
    )


async def _register_corp(ceo: int = 99999) -> uuid.UUID:
    async with SessionLocal() as session:
        corp = await corporations_repo.create_corporation(
            session,
            eve_corporation_id=CORP_EVE_ID,
            name="Test Corp",
            ceo_character_id=ceo,
            registered_by_character_id=ceo,
        )
        await session.commit()
        return corp.id


class FakeSso:
    configured = True

    def __init__(self, *, refresh="refresh-1", new_access="access-new",
                 new_refresh=None, refresh_error=None):
        self._refresh = refresh
        self._new_access = new_access
        self._new_refresh = new_refresh
        self._refresh_error = refresh_error
        self.revoked: list[str] = []

    def build_authorize_url(self, *, state, code_challenge, scopes=None):
        return f"https://login.eveonline.com/authorize?state={state}"

    async def revoke_refresh_token(self, refresh_token):
        self.revoked.append(refresh_token)

    async def exchange_code(self, code, code_verifier):
        return OAuthToken(access_token="access-initial", refresh_token=self._refresh)

    async def verify_token(self, access_token):
        return VerifiedCharacter(character_id=CHAR_ID, name="Boss")

    async def refresh_access_token(self, refresh_token):
        if self._refresh_error is not None:
            raise self._refresh_error
        return OAuthToken(
            access_token=self._new_access, refresh_token=self._new_refresh
        )


# --- authorize challenge (state routing) ---


def test_begin_authorize_state_is_prefixed():
    # The shared /auth/callback routes on this prefix; login states never carry it,
    # so a structure round-trip can't be misrouted to the login completion (400).
    challenge = corp_esi_token_app.begin_corp_esi_authorize(FakeSso())
    assert challenge.state.startswith(corp_esi_token_app.STRUCTURE_STATE_PREFIX)


# --- unconfigured (placeholder) encryption key: refused before any EVE round-trip.
# Key *validity* is enforced at boot (see test_config.py); these cover the
# valid-but-placeholder key outside development.


def _unconfigured_settings():
    # model_copy skips validators, which is exactly what we want here: a settings
    # object whose corp_esi_token_configured is False (prod + placeholder key).
    return get_settings().model_copy(update={"environment": "production"})


def test_begin_authorize_refuses_unconfigured_key(monkeypatch):
    monkeypatch.setattr(corp_esi_token_app, "get_settings", _unconfigured_settings)
    with pytest.raises(StructureEncryptionNotConfigured):
        corp_esi_token_app.begin_corp_esi_authorize(FakeSso())


async def test_access_token_refuses_unconfigured_key(monkeypatch):
    corp_uuid = await _authorize()
    monkeypatch.setattr(corp_esi_token_app, "get_settings", _unconfigured_settings)
    async with SessionLocal() as session:
        with pytest.raises(StructureEncryptionNotConfigured):
            await corp_esi_token_app.get_corp_esi_access_token(
                session, FakeSso(), corporation_uuid=corp_uuid, cipher=CIPHER
            )


# --- TokenCipher ---


def test_cipher_round_trip():
    cipher = TokenCipher(Fernet.generate_key().decode())
    ciphertext = cipher.encrypt("refresh-abc")
    assert isinstance(ciphertext, bytes) and ciphertext != b"refresh-abc"
    assert cipher.decrypt(ciphertext) == "refresh-abc"


def test_cipher_wrong_key_fails():
    a = TokenCipher(Fernet.generate_key().decode())
    b = TokenCipher(Fernet.generate_key().decode())
    with pytest.raises(InvalidToken):
        b.decrypt(a.encrypt("secret"))


# --- corp ESI access connect (CEO/Director-gated, encrypts) ---


async def test_member_cannot_authorize():
    await _register_corp()
    async with SessionLocal() as session:
        with pytest.raises(NotAuthorizedToAuthorizeStructure):
            await corp_esi_token_app.complete_corp_esi_authorize(
                session, FakeSso(), MemberEsi(), code="c", verifier="v",
                user=_user("member"), cipher=CIPHER,
            )


async def test_plain_manager_cannot_authorize():
    # Connect/revoke is CEO/Director only now (ADR-0036); a manager who isn't a
    # director can't connect the corp ESI token.
    await _register_corp()
    async with SessionLocal() as session:
        with pytest.raises(NotAuthorizedToAuthorizeStructure):
            await corp_esi_token_app.complete_corp_esi_authorize(
                session, FakeSso(), CeoEsi(), code="c", verifier="v",
                user=_user("manager"), cipher=CIPHER,
            )


async def test_out_of_corp_character_rejected():
    await _register_corp()

    class OtherCorpEsi(CeoEsi):
        async def get_character_corporation(self, character_id: int) -> int:
            return 98000999  # the authorizing character is in a different corp

    async with SessionLocal() as session:
        with pytest.raises(AuthorizingCharacterNotInCorporation):
            await corp_esi_token_app.complete_corp_esi_authorize(
                session, FakeSso(), OtherCorpEsi(), code="c", verifier="v",
                user=_user("ceo"), cipher=CIPHER,
            )


def test_connect_requests_both_scope_sets():
    scopes = get_settings().eve_corp_token_scopes
    assert "esi-markets.structure_markets.v1" in scopes  # structures
    assert "esi-corporations.read_corporation_membership.v1" in scopes  # roster
    assert scopes.split().count("publicData") == 1  # deduped across the two sets


async def test_director_authorize_stores_encrypted_token():
    corp_uuid = await _register_corp()
    async with SessionLocal() as session:
        result = await corp_esi_token_app.complete_corp_esi_authorize(
            session, FakeSso(refresh="refresh-secret"), CeoEsi(), code="c", verifier="v",
            user=_user("member", is_director=True), cipher=CIPHER,
        )
    assert result.token.character_name == "Boss"
    assert result.replaced_character_name is None  # first authorization, no swap
    async with SessionLocal() as session:
        stored = await tokens_repo.get_for_corp(session, corp_uuid)
    assert stored is not None
    assert stored.encrypted_refresh_token != b"refresh-secret"  # encrypted at rest
    assert CIPHER.decrypt(stored.encrypted_refresh_token) == "refresh-secret"


async def test_reauthorize_with_different_character_warns():
    await _register_corp()
    # First authorize as Boss (the default FakeSso character), refresh "refresh-1".
    async with SessionLocal() as session:
        first = await corp_esi_token_app.complete_corp_esi_authorize(
            session, FakeSso(), CeoEsi(), code="c", verifier="v",
            user=_user("ceo"), cipher=CIPHER,
        )
    assert first.replaced_character_name is None

    # Re-authorize, but the SSO picker returned a *different* character + token.
    class OtherCharSso(FakeSso):
        async def verify_token(self, access_token):
            return VerifiedCharacter(character_id=67890, name="Alt Pilot")

    reauth = OtherCharSso(refresh="refresh-2")
    async with SessionLocal() as session:
        again = await corp_esi_token_app.complete_corp_esi_authorize(
            session, reauth, CeoEsi(), code="c", verifier="v",
            user=_user("ceo"), cipher=CIPHER,
        )
    # The new token replaces the old, we surface the previous character, AND the
    # previous grant is signed out at EVE.
    assert again.token.character_name == "Alt Pilot"
    assert again.replaced_character_name == "Boss"
    assert reauth.revoked == ["refresh-1"]

    # Re-authorizing again as the *same* (new) character does not warn, but still
    # revokes the now-superseded refresh token.
    reauth2 = OtherCharSso(refresh="refresh-3")
    async with SessionLocal() as session:
        same = await corp_esi_token_app.complete_corp_esi_authorize(
            session, reauth2, CeoEsi(), code="c", verifier="v",
            user=_user("ceo"), cipher=CIPHER,
        )
    assert same.replaced_character_name is None
    assert reauth2.revoked == ["refresh-2"]


async def test_revoke_signs_grant_out_at_eve_and_deletes():
    corp_uuid = await _authorize("refresh-to-kill")
    sso = FakeSso()
    async with SessionLocal() as session:
        await corp_esi_token_app.revoke(
            session, sso, corporation_id=CORP_EVE_ID, cipher=CIPHER
        )
    assert sso.revoked == ["refresh-to-kill"]  # killed at EVE, not just locally
    async with SessionLocal() as session:
        assert await tokens_repo.get_for_corp(session, corp_uuid) is None


async def test_revoke_without_token_raises():
    await _register_corp()
    async with SessionLocal() as session:
        with pytest.raises(CorpEsiTokenMissing):
            await corp_esi_token_app.revoke(
                session, FakeSso(), corporation_id=CORP_EVE_ID, cipher=CIPHER
            )


# --- get_corp_esi_access_token (refresh / rotation / expiry / missing) ---


async def _authorize(corp_refresh: str = "r1") -> uuid.UUID:
    corp_uuid = await _register_corp()
    async with SessionLocal() as session:
        await corp_esi_token_app.complete_corp_esi_authorize(
            session, FakeSso(refresh=corp_refresh), CeoEsi(), code="c", verifier="v",
            user=_user("ceo"), cipher=CIPHER,
        )
    return corp_uuid


async def test_get_access_token_refreshes():
    corp_uuid = await _authorize()
    async with SessionLocal() as session:
        access = await corp_esi_token_app.get_corp_esi_access_token(
            session, FakeSso(new_access="fresh-access"),
            corporation_uuid=corp_uuid, cipher=CIPHER,
        )
    assert access == "fresh-access"


async def test_refresh_rotation_persists_new_token():
    corp_uuid = await _authorize("r1")
    async with SessionLocal() as session:
        await corp_esi_token_app.get_corp_esi_access_token(
            session, FakeSso(new_refresh="r2-rotated"),
            corporation_uuid=corp_uuid, cipher=CIPHER,
        )
    async with SessionLocal() as session:
        stored = await tokens_repo.get_for_corp(session, corp_uuid)
    assert CIPHER.decrypt(stored.encrypted_refresh_token) == "r2-rotated"


async def test_invalid_grant_marks_expired_and_raises():
    corp_uuid = await _authorize()
    err = httpx.HTTPStatusError(
        "invalid_grant",
        request=httpx.Request("POST", "http://x"),
        response=httpx.Response(400),
    )
    async with SessionLocal() as session:
        with pytest.raises(CorpEsiTokenExpired):
            await corp_esi_token_app.get_corp_esi_access_token(
                session, FakeSso(refresh_error=err),
                corporation_uuid=corp_uuid, cipher=CIPHER,
            )
    async with SessionLocal() as session:
        stored = await tokens_repo.get_for_corp(session, corp_uuid)
    assert stored.last_refresh_failed_at is not None


async def test_missing_token_raises():
    corp_uuid = await _register_corp()
    async with SessionLocal() as session:
        with pytest.raises(CorpEsiTokenMissing):
            await corp_esi_token_app.get_corp_esi_access_token(
                session, FakeSso(), corporation_uuid=corp_uuid, cipher=CIPHER,
            )


# --- structure search by name ---


class FakeEsiMarketSearch:
    def __init__(self, ids, names):
        self._ids = ids
        self._names = names

    async def search_structures(self, *, character_id, query, access_token, limit=10):
        return self._ids

    async def resolve_structure_name(self, *, structure_id, access_token):
        return self._names.get(structure_id)


async def test_search_structures_returns_named_results():
    await _authorize()
    esi = FakeEsiMarketSearch(
        ids=[1035000000001, 1035000000002],
        names={1035000000001: "1DQ1-A - Palace", 1035000000002: None},  # 2nd inaccessible
    )
    async with SessionLocal() as session:
        results = await corp_esi_token_app.search_structures(
            session, FakeSso(), esi, corporation_id=CORP_EVE_ID, query="pal", cipher=CIPHER
        )
    # Only the resolvable structure is returned, typed, id as a string.
    assert results == [
        corp_esi_token_app.StructureMatch(
            structure_id="1035000000001", name="1DQ1-A - Palace"
        )
    ]


async def test_search_structures_requires_authorization():
    await _register_corp()  # corp registered but no structure token
    async with SessionLocal() as session:
        with pytest.raises(CorpEsiTokenMissing):
            await corp_esi_token_app.search_structures(
                session, FakeSso(), FakeEsiMarketSearch([], {}),
                corporation_id=CORP_EVE_ID, query="x", cipher=CIPHER,
            )


# --- API wiring (manager-gated status) ---


async def test_status_endpoint_authorized_false_for_manager():
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        resp = await http.get("/api/v1/corporations/me/structure-token")
    assert resp.status_code == 200
    body = resp.json()
    assert body["authorized"] is False
    assert body["configured"] is True  # dev placeholder counts as configured


async def test_status_reports_unconfigured_server(monkeypatch):
    # Placeholder key outside development → the UI disables structure options.
    from app.interface.v1 import structures as structures_iface

    monkeypatch.setattr(
        structures_iface,
        "get_settings",
        lambda: get_settings().model_copy(update={"environment": "production"}),
    )
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        resp = await http.get("/api/v1/corporations/me/structure-token")
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is False
    assert body["authorized"] is False


async def test_status_endpoint_forbidden_for_member():
    await _register_corp()
    async with make_client(MemberEsi()) as http:
        await login(http)
        resp = await http.get("/api/v1/corporations/me/structure-token")
    assert resp.status_code == 403
