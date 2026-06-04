import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.sso import OAuthToken, VerifiedCharacter, get_sso_client
from app.eve.esi import CharacterInfo, CorporationInfo, get_esi_client
from app.main import app


class FakeSso:
    configured = True

    def build_authorize_url(self, *, state: str, code_challenge: str) -> str:
        return (
            "https://login.eveonline.com/v2/oauth/authorize"
            f"?state={state}&code_challenge={code_challenge}"
        )

    async def exchange_code(self, code: str, code_verifier: str) -> OAuthToken:
        return OAuthToken(access_token="fake-access-token")

    async def verify_token(self, access_token: str) -> VerifiedCharacter:
        return VerifiedCharacter(character_id=12345, name="Test Pilot")


class FakeEsi:
    """Logged-in character is a regular member (ceo_id differs)."""

    ceo_id = 99999

    async def get_character(self, character_id: int) -> CharacterInfo:
        return CharacterInfo(name="Test Pilot", corporation_id=98000001)

    async def get_character_corporation(self, character_id: int) -> int:
        return 98000001

    async def get_corporation(self, corporation_id: int) -> CorporationInfo:
        return CorporationInfo(name="Test Corp", ceo_id=self.ceo_id, ticker="TEST")

    async def get_character_roles(self, character_id: int, access_token: str) -> list[str]:
        return []


class FakeEsiCeo(FakeEsi):
    """Logged-in character is the CEO (ceo_id matches character_id 12345)."""

    ceo_id = 12345


@pytest.fixture
def client(request: pytest.FixtureRequest):
    esi_cls = getattr(request, "param", FakeEsi)
    app.dependency_overrides[get_sso_client] = lambda: FakeSso()
    app.dependency_overrides[get_esi_client] = lambda: esi_cls()
    transport = ASGITransport(app=app)
    yield AsyncClient(transport=transport, base_url="http://test")
    app.dependency_overrides.clear()


async def _login(http: AsyncClient) -> dict:
    url_resp = await http.get("/api/v1/auth/login-url")
    assert url_resp.status_code == 200
    state = url_resp.json()["state"]
    assert url_resp.json()["authorization_url"].startswith(
        "https://login.eveonline.com"
    )
    login_resp = await http.post(
        "/api/v1/auth/login", json={"code": "auth-code", "state": state}
    )
    assert login_resp.status_code == 200
    return login_resp.json()


async def test_me_requires_auth(client):
    async with client as http:
        resp = await http.get("/api/v1/auth/me")
    assert resp.status_code == 401


async def test_login_flow_member(client):
    async with client as http:
        body = await _login(http)
        assert body["character_id"] == 12345
        assert body["character_name"] == "Test Pilot"
        assert body["corporation_id"] == 98000001
        assert body["corporation_name"] == "Test Corp"
        assert body["role"] == "member"
        assert body["corporation_registered"] is False

        me = await http.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["character_name"] == "Test Pilot"

        logout = await http.post("/api/v1/auth/logout")
        assert logout.status_code == 204

        me_after = await http.get("/api/v1/auth/me")
        assert me_after.status_code == 401


@pytest.mark.parametrize("client", [FakeEsiCeo], indirect=True)
async def test_login_flow_ceo(client):
    async with client as http:
        body = await _login(http)
        assert body["role"] == "ceo"


async def test_login_rejects_bad_state(client):
    async with client as http:
        await http.get("/api/v1/auth/login-url")
        resp = await http.post(
            "/api/v1/auth/login", json={"code": "auth-code", "state": "wrong-state"}
        )
    assert resp.status_code == 400
