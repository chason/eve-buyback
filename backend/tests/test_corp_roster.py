"""Corp-roster fetch via the stored Corp ESI token + manager-designation search and the
daily background refresh (ADR-0036)."""

import types
import uuid

import pytest
from cryptography.fernet import Fernet

from app.application import corp_roster as roster_app
from app.application import structure_tokens as structures_app
from app.application.auth import AuthenticatedUser
from app.application.errors import (
    CorpEsiTokenMissing,
    RosterAccessDenied,
    RosterRefreshTooSoon,
)
from app.data.db import SessionLocal
from app.data.repositories import corp_esi_token as tokens_repo
from app.data.repositories import corporations as corporations_repo
from app.domain.roles import Role
from app.interface import jobs
from app.plugins.esi import (
    CharacterInfo,
    CorporationInfo,
    CorporationMembersForbidden,
)
from app.plugins.sso import OAuthToken, VerifiedCharacter
from app.plugins.token_cipher import TokenCipher
from tests.helpers import MemberEsi, login, make_client

CORP_EVE_ID = 98000001
CHAR_ID = 12345
CIPHER = TokenCipher(Fernet.generate_key().decode())


def _user(role: Role = "ceo", *, is_director: bool = False) -> AuthenticatedUser:
    return AuthenticatedUser(
        character_id=CHAR_ID,
        character_name="Boss",
        corporation_id=CORP_EVE_ID,
        corporation_name="Test Corp",
        role=role,
        is_director=is_director,
        corporation_registered=True,
    )


class FakeSso:
    configured = True

    def build_authorize_url(self, *, state, code_challenge, scopes=None):
        return f"https://login.eveonline.com/authorize?state={state}"

    async def exchange_code(self, code, code_verifier):
        return OAuthToken(access_token="access-initial", refresh_token="refresh-1")

    async def verify_token(self, access_token):
        return VerifiedCharacter(character_id=CHAR_ID, name="Boss")

    async def refresh_access_token(self, refresh_token):
        return OAuthToken(access_token="access-fresh", refresh_token=None)


class RosterEsi:
    """ESI fake with corp-membership + name resolution for roster tests."""

    def __init__(self, *, members=None, names=None, forbid=False, char_corp=CORP_EVE_ID):
        self.members = members if members is not None else [101, 102, 103]
        self.names = (
            names if names is not None else {101: "Alice", 102: "Bob", 103: "Albert"}
        )
        self.forbid = forbid
        self.char_corp = char_corp

    async def get_character(self, character_id):
        return CharacterInfo(name="Boss", corporation_id=self.char_corp)

    async def get_character_corporation(self, character_id):
        return self.char_corp

    async def get_corporation(self, corporation_id):
        return CorporationInfo(name="Test Corp", ceo_id=CHAR_ID, ticker="T")

    async def get_character_roles(self, character_id, access_token):
        return []

    async def get_corporation_members(self, corporation_id, access_token):
        if self.forbid:
            raise CorporationMembersForbidden()
        return self.members

    async def resolve_universe_names(self, ids):
        return {i: self.names[i] for i in ids if i in self.names}


async def _register_corp() -> uuid.UUID:
    async with SessionLocal() as session:
        corp = await corporations_repo.create_corporation(
            session,
            eve_corporation_id=CORP_EVE_ID,
            name="Test Corp",
            ceo_character_id=CHAR_ID,
            registered_by_character_id=CHAR_ID,
        )
        await session.commit()
        return corp.id


async def _connect(esi: RosterEsi) -> uuid.UUID:
    """Register the corp + connect the Corp ESI token (no roster auto-populate — that's
    the interface endpoint's job; here we drive `refresh_roster` explicitly)."""
    corp_uuid = await _register_corp()
    async with SessionLocal() as session:
        await structures_app.complete_corp_esi_authorize(
            session, FakeSso(), esi, code="c", verifier="v",
            user=_user("ceo"), cipher=CIPHER,
        )
    return corp_uuid


async def _refresh(esi, **kw):
    async with SessionLocal() as session:
        return await roster_app.refresh_roster(
            session, FakeSso(), esi, corporation_id=CORP_EVE_ID, cipher=CIPHER, **kw
        )


async def test_refresh_via_stored_token_then_search():
    esi = RosterEsi()
    await _connect(esi)
    status = await _refresh(esi, enforce_cooldown=False)
    assert status.synced is True
    assert status.member_count == 3

    async with SessionLocal() as session:
        results = await roster_app.search_members(
            session, corporation_id=CORP_EVE_ID, query="al"
        )
    # ILIKE %al% matches Alice + Albert (case-insensitive), ordered by name.
    assert [m.name for m in results] == ["Albert", "Alice"]
    assert {m.character_id for m in results} == {101, 103}


async def test_refresh_replaces_snapshot():
    esi = RosterEsi()
    await _connect(esi)
    await _refresh(esi, enforce_cooldown=False)

    esi.members = [101, 104]  # Bob left, Carol joined
    esi.names = {101: "Alice", 104: "Carol"}
    await _refresh(esi, enforce_cooldown=False)

    async with SessionLocal() as session:
        gone = await roster_app.search_members(
            session, corporation_id=CORP_EVE_ID, query="bob"
        )
        carol = await roster_app.search_members(
            session, corporation_id=CORP_EVE_ID, query="carol"
        )
    assert gone == []
    assert [m.name for m in carol] == ["Carol"]


async def test_non_director_403_does_not_flag_token():
    esi = RosterEsi(forbid=True)
    corp_uuid = await _connect(esi)
    with pytest.raises(RosterAccessDenied):
        await _refresh(esi, enforce_cooldown=False)
    # A members-403 is a per-scope access issue, NOT a refresh-token failure: the token
    # stays healthy so structure pricing still works.
    async with SessionLocal() as session:
        token = await tokens_repo.get_for_corp(session, corp_uuid)
    assert token is not None
    assert token.last_refresh_failed_at is None


async def test_refresh_without_token_raises():
    await _register_corp()  # registered, but no Corp ESI token connected
    with pytest.raises(CorpEsiTokenMissing):
        await _refresh(RosterEsi(), enforce_cooldown=False)


async def test_manual_cooldown_blocks_quick_refresh_but_not_background():
    esi = RosterEsi()
    await _connect(esi)
    await _refresh(esi, enforce_cooldown=False)
    # A manual refresh right after is rejected…
    with pytest.raises(RosterRefreshTooSoon):
        await _refresh(esi, enforce_cooldown=True)
    # …but the background path (no cooldown) is not.
    status = await _refresh(esi, enforce_cooldown=False)
    assert status.synced is True


async def test_status_empty_before_first_refresh():
    await _connect(RosterEsi())
    async with SessionLocal() as session:
        status = await roster_app.get_roster_status(
            session, corporation_id=CORP_EVE_ID
        )
    assert status.synced is False
    assert status.member_count == 0


async def test_list_corp_eve_ids_with_token():
    await _connect(RosterEsi())
    async with SessionLocal() as session:
        ids = await tokens_repo.list_corp_eve_ids_with_token(session)
    assert ids == [CORP_EVE_ID]


async def test_background_job_refreshes_each_token_corp(monkeypatch):
    await _connect(RosterEsi())
    calls = []

    async def fake_refresh(
        session, sso, esi, *, corporation_id, cipher, now, enforce_cooldown
    ):
        calls.append((corporation_id, enforce_cooldown))

    monkeypatch.setattr(jobs.corp_roster, "refresh_roster", fake_refresh)
    app = types.SimpleNamespace(state=types.SimpleNamespace(http=None))
    await jobs.run_roster_refresh(app)
    # Iterates token-holding corps and refreshes server-side, bypassing the cooldown.
    assert calls == [(CORP_EVE_ID, False)]


async def test_background_job_survives_one_corp_failing(monkeypatch):
    await _connect(RosterEsi())

    async def boom(session, sso, esi, *, corporation_id, cipher, now, enforce_cooldown):
        raise RosterAccessDenied()

    monkeypatch.setattr(jobs.corp_roster, "refresh_roster", boom)
    app = types.SimpleNamespace(state=types.SimpleNamespace(http=None))
    await jobs.run_roster_refresh(app)  # must not raise


async def test_member_cannot_access_roster_endpoints():
    async with make_client(MemberEsi()) as http:
        await login(http)  # role member, not a director
        assert (await http.get("/api/v1/corporations/me/roster")).status_code == 403
        assert (
            await http.post("/api/v1/corporations/me/roster/refresh")
        ).status_code == 403
        assert (
            await http.get("/api/v1/corporations/me/roster/members?q=al")
        ).status_code == 403
