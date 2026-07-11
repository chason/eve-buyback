"""ADR-0041/0042: the app-admin access endpoints — the cross-tenant admin surface.
Proves the gate (401/403 for non-admins) and the grant/extend/revoke round-trip."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.data.db import SessionLocal
from app.data.repositories import corporations as corporations_repo
from app.data.repositories import entitlements as entitlements_repo
from app.interface import security
from app.main import app
from app.plugins.esi import CharacterInfo, CorporationInfo, get_esi_client
from app.plugins.sso import OAuthToken, VerifiedCharacter, get_sso_client

ADMIN_CHAR_ID = 12345
OTHER_CORP_EVE_ID = 98000099


class FakeSso:
    configured = True

    def build_authorize_url(self, *, state: str, code_challenge: str) -> str:
        return f"https://login.eveonline.com/v2/oauth/authorize?state={state}"

    async def exchange_code(self, code: str, code_verifier: str) -> OAuthToken:
        return OAuthToken(access_token="fake-access-token")

    async def verify_token(self, access_token: str) -> VerifiedCharacter:
        return VerifiedCharacter(character_id=ADMIN_CHAR_ID, name="Operator")


class FakeEsi:
    async def get_character(self, character_id: int) -> CharacterInfo:
        return CharacterInfo(name="Operator", corporation_id=98000001)

    async def get_character_corporation(self, character_id: int) -> int:
        return 98000001

    async def get_corporation(self, corporation_id: int) -> CorporationInfo:
        return CorporationInfo(name="Operator Corp", ceo_id=99999, ticker="OP")

    async def get_character_roles(self, character_id: int, access_token: str) -> list[str]:
        return []


@pytest.fixture
def client(request: pytest.FixtureRequest, monkeypatch):
    """Logged-in client; parametrize with the admin allowlist ("" = not an admin)."""
    admin_ids = getattr(request, "param", str(ADMIN_CHAR_ID))
    settings = Settings(
        environment="development", admin_character_ids=admin_ids, _env_file=None
    )
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    app.dependency_overrides[get_sso_client] = lambda: FakeSso()
    app.dependency_overrides[get_esi_client] = lambda: FakeEsi()
    yield AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Buyback-CSRF": "1"},
    )
    app.dependency_overrides.clear()


async def _login(http: AsyncClient) -> None:
    state = (await http.post("/api/v1/auth/login")).json()["state"]
    resp = await http.post(
        "/api/v1/auth/session", json={"code": "auth-code", "state": state}
    )
    assert resp.status_code == 200


async def _register_corp(eve_id: int = OTHER_CORP_EVE_ID, name: str = "Some Corp") -> None:
    async with SessionLocal() as session:
        await corporations_repo.create_corporation(
            session,
            eve_corporation_id=eve_id,
            name=name,
            ceo_character_id=1,
            registered_by_character_id=1,
        )
        await session.commit()


async def test_admin_endpoints_require_auth(client):
    async with client as http:
        assert (await http.get("/api/v1/admin/access")).status_code == 401


@pytest.mark.parametrize("client", [""], indirect=True)  # not in the allowlist
async def test_admin_endpoints_reject_non_admin(client):
    async with client as http:
        await _login(http)
        assert (await http.get("/api/v1/admin/access")).status_code == 403
        assert (
            await http.put(f"/api/v1/admin/access/{OTHER_CORP_EVE_ID}", json={})
        ).status_code == 403
        assert (
            await http.delete(f"/api/v1/admin/access/{OTHER_CORP_EVE_ID}")
        ).status_code == 403


async def test_grant_list_revoke_round_trip(client):
    await _register_corp()
    async with client as http:
        await _login(http)

        # Before any grant: the corp lists with no access.
        listing = (await http.get("/api/v1/admin/access")).json()
        corp = next(c for c in listing if c["corporation_id"] == OTHER_CORP_EVE_ID)
        assert corp["active"] is False
        assert corp["source"] is None

        # Perpetual grant (no expiry).
        granted = await http.put(f"/api/v1/admin/access/{OTHER_CORP_EVE_ID}", json={})
        assert granted.status_code == 200
        body = granted.json()
        assert body["active"] is True
        assert body["source"] == "admin"
        assert body["expires_at"] is None
        assert body["granted_by_character_id"] == ADMIN_CHAR_ID

        # Extend with a dated expiry (rewrites the one row).
        until = (datetime.now(UTC) + timedelta(days=30)).isoformat()
        extended = await http.put(
            f"/api/v1/admin/access/{OTHER_CORP_EVE_ID}", json={"expires_at": until}
        )
        assert extended.json()["active"] is True
        assert extended.json()["expires_at"] is not None

        # Revoke → inactive; revoking again is an idempotent 204.
        assert (
            await http.delete(f"/api/v1/admin/access/{OTHER_CORP_EVE_ID}")
        ).status_code == 204
        listing = (await http.get("/api/v1/admin/access")).json()
        corp = next(c for c in listing if c["corporation_id"] == OTHER_CORP_EVE_ID)
        assert corp["active"] is False
        assert (
            await http.delete(f"/api/v1/admin/access/{OTHER_CORP_EVE_ID}")
        ).status_code == 204


async def test_expired_grant_lists_inactive(client):
    await _register_corp()
    # Arrange the lapsed grant directly (the API refuses past-dated grants).
    async with SessionLocal() as session:
        corp = await corporations_repo.get_by_eve_id(session, OTHER_CORP_EVE_ID)
        await entitlements_repo.upsert(
            session,
            corporation_id=corp.id,
            feature="accounting",
            source="admin",
            expires_at=datetime.now(UTC) - timedelta(days=1),
        )
        await session.commit()
    async with client as http:
        await _login(http)
        listing = (await http.get("/api/v1/admin/access")).json()
        corp_row = next(c for c in listing if c["corporation_id"] == OTHER_CORP_EVE_ID)
        assert corp_row["active"] is False
        assert corp_row["source"] == "admin"  # the lapsed grant is still visible


async def test_grant_with_past_expiry_is_refused(client):
    await _register_corp()
    async with client as http:
        await _login(http)
        past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        resp = await http.put(
            f"/api/v1/admin/access/{OTHER_CORP_EVE_ID}", json={"expires_at": past}
        )
        assert resp.status_code == 422
        assert "in the past" in resp.json()["detail"]


async def test_grant_unregistered_corp_404s(client):
    async with client as http:
        await _login(http)
        resp = await http.put("/api/v1/admin/access/98999999", json={})
        assert resp.status_code == 404
