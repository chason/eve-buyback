"""ADR-0041: the instance app-admin axis — an env-var allowlist resolved per request,
orthogonal to the corp member/manager/ceo roles."""

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.application.auth import AuthenticatedUser
from app.config import Settings
from app.domain.app_admin import is_app_admin
from app.interface import security
from app.main import app
from app.plugins.esi import CharacterInfo, CorporationInfo, get_esi_client
from app.plugins.sso import OAuthToken, VerifiedCharacter, get_sso_client

CHAR_ID = 12345


# --- domain rule --------------------------------------------------------------


def test_is_app_admin_true_when_listed():
    assert is_app_admin(CHAR_ID, {CHAR_ID, 42}) is True


def test_is_app_admin_false_when_not_listed():
    assert is_app_admin(CHAR_ID, {42}) is False
    assert is_app_admin(CHAR_ID, frozenset()) is False


# --- require_app_admin dependency (unit) --------------------------------------


def _user(character_id: int) -> AuthenticatedUser:
    return AuthenticatedUser(
        character_id=character_id,
        character_name="Pilot",
        corporation_id=1,
        corporation_name="Corp",
        role="member",
    )


def test_require_app_admin_allows_listed(monkeypatch):
    s = Settings(environment="development", admin_character_ids="12345", _env_file=None)
    monkeypatch.setattr(security, "get_settings", lambda: s)
    user = _user(CHAR_ID)
    assert security.require_app_admin(user) is user


def test_require_app_admin_rejects_unlisted(monkeypatch):
    s = Settings(environment="development", admin_character_ids="999", _env_file=None)
    monkeypatch.setattr(security, "get_settings", lambda: s)
    with pytest.raises(HTTPException) as exc:
        security.require_app_admin(_user(CHAR_ID))
    assert exc.value.status_code == 403


# --- /me integration ----------------------------------------------------------


class FakeSso:
    configured = True

    def build_authorize_url(self, *, state: str, code_challenge: str) -> str:
        return f"https://login.eveonline.com/v2/oauth/authorize?state={state}"

    async def exchange_code(self, code: str, code_verifier: str) -> OAuthToken:
        return OAuthToken(access_token="fake-access-token")

    async def verify_token(self, access_token: str) -> VerifiedCharacter:
        return VerifiedCharacter(character_id=CHAR_ID, name="Pilot")


class FakeEsi:
    async def get_character(self, character_id: int) -> CharacterInfo:
        return CharacterInfo(name="Pilot", corporation_id=98000001)

    async def get_character_corporation(self, character_id: int) -> int:
        return 98000001

    async def get_corporation(self, corporation_id: int) -> CorporationInfo:
        return CorporationInfo(name="Corp", ceo_id=99999, ticker="C")

    async def get_character_roles(self, character_id: int, access_token: str) -> list[str]:
        return []


async def _login_and_me(admin_ids: str, monkeypatch) -> tuple[dict, dict]:
    s = Settings(
        environment="development", admin_character_ids=admin_ids, _env_file=None
    )
    monkeypatch.setattr(security, "get_settings", lambda: s)
    app.dependency_overrides[get_sso_client] = lambda: FakeSso()
    app.dependency_overrides[get_esi_client] = lambda: FakeEsi()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-Buyback-CSRF": "1"},
        ) as http:
            state = (await http.post("/api/v1/auth/login")).json()["state"]
            login = await http.post(
                "/api/v1/auth/session", json={"code": "auth-code", "state": state}
            )
            me = await http.get("/api/v1/auth/me")
            return login.json(), me.json()
    finally:
        app.dependency_overrides.clear()


async def test_me_flags_app_admin_when_listed(monkeypatch):
    login, me = await _login_and_me("12345", monkeypatch)
    assert login["is_app_admin"] is True
    assert me["is_app_admin"] is True


async def test_me_not_app_admin_when_unlisted(monkeypatch):
    login, me = await _login_and_me("", monkeypatch)
    assert login["is_app_admin"] is False
    assert me["is_app_admin"] is False
