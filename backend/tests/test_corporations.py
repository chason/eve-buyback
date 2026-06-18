import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.plugins.esi import CharacterInfo, CorporationInfo, get_esi_client
from app.plugins.sso import OAuthToken, VerifiedCharacter, get_sso_client

CHAR_ID = 12345
CORP_ID = 98000001


class FakeSso:
    configured = True

    def build_authorize_url(self, *, state: str, code_challenge: str) -> str:
        return f"https://login.eveonline.com/v2/oauth/authorize?state={state}"

    async def exchange_code(self, code: str, code_verifier: str) -> OAuthToken:
        return OAuthToken(access_token="fake-access-token")

    async def verify_token(self, access_token: str) -> VerifiedCharacter:
        return VerifiedCharacter(character_id=CHAR_ID, name="Boss")


class BaseEsi:
    ceo_id = 99999
    roles: list[str] = []
    target_corp = CORP_ID  # corp returned for get_character (manager targets)

    async def get_character(self, character_id: int) -> CharacterInfo:
        return CharacterInfo(name="Grunt", corporation_id=self.target_corp)

    async def get_character_corporation(self, character_id: int) -> int:
        return CORP_ID

    async def get_corporation(self, corporation_id: int) -> CorporationInfo:
        return CorporationInfo(name="Test Corp", ceo_id=self.ceo_id, ticker="T")

    async def get_character_roles(self, character_id: int, access_token: str) -> list[str]:
        return self.roles


class CeoEsi(BaseEsi):
    ceo_id = CHAR_ID


class DirectorEsi(BaseEsi):
    roles = ["Director"]


class MemberEsi(BaseEsi):
    pass


class OtherCorpEsi(CeoEsi):
    target_corp = 98000002  # manager target is in a different corp


def _client(esi: BaseEsi) -> AsyncClient:
    app.dependency_overrides[get_sso_client] = lambda: FakeSso()
    app.dependency_overrides[get_esi_client] = lambda: esi
    # Default CSRF header so mutating requests pass the middleware (ADR-0017).
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Buyback-CSRF": "1"},
    )


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


async def _login(http: AsyncClient) -> dict:
    begin = await http.post("/api/v1/auth/login")
    state = begin.json()["state"]
    session_resp = await http.post(
        "/api/v1/auth/session", json={"code": "auth-code", "state": state}
    )
    assert session_resp.status_code == 200
    return session_resp.json()


async def test_ceo_can_register():
    async with _client(CeoEsi()) as http:
        me = await _login(http)
        assert me["role"] == "ceo"
        assert me["corporation_registered"] is False

        resp = await http.post("/api/v1/corporations")
        assert resp.status_code == 201
        assert resp.json()["corporation_id"] == CORP_ID

        me_after = await http.get("/api/v1/auth/me")
        assert me_after.json()["corporation_registered"] is True

        corp = await http.get("/api/v1/corporations/me")
        assert corp.status_code == 200
        assert corp.json()["ceo_character_id"] == CHAR_ID


async def test_director_register_grants_manager():
    async with _client(DirectorEsi()) as http:
        me = await _login(http)
        assert me["role"] == "member"
        assert me["is_director"] is True

        resp = await http.post("/api/v1/corporations")
        assert resp.status_code == 201

        me_after = await http.get("/api/v1/auth/me")
        assert me_after.json()["role"] == "manager"
        assert me_after.json()["corporation_registered"] is True


async def test_member_cannot_register():
    async with _client(MemberEsi()) as http:
        await _login(http)
        resp = await http.post("/api/v1/corporations")
        assert resp.status_code == 403


async def test_register_is_idempotent_conflict():
    async with _client(CeoEsi()) as http:
        await _login(http)
        assert (await http.post("/api/v1/corporations")).status_code == 201
        assert (await http.post("/api/v1/corporations")).status_code == 409


async def test_my_corporation_404_when_unregistered():
    async with _client(MemberEsi()) as http:
        await _login(http)
        resp = await http.get("/api/v1/corporations/me")
        assert resp.status_code == 404


async def test_ceo_manager_lifecycle():
    async with _client(CeoEsi()) as http:
        await _login(http)
        await http.post("/api/v1/corporations")

        add = await http.post(
            "/api/v1/corporations/me/managers", json={"character_id": 555}
        )
        assert add.status_code == 201
        assert add.json()["character_id"] == 555
        assert add.json()["character_name"] == "Grunt"

        listed = await http.get("/api/v1/corporations/me/managers")
        assert listed.status_code == 200
        assert [m["character_id"] for m in listed.json()] == [555]

        removed = await http.delete("/api/v1/corporations/me/managers/555")
        assert removed.status_code == 204

        listed_after = await http.get("/api/v1/corporations/me/managers")
        assert listed_after.json() == []


async def test_add_manager_rejects_other_corp_character():
    async with _client(OtherCorpEsi()) as http:
        await _login(http)
        await http.post("/api/v1/corporations")
        resp = await http.post(
            "/api/v1/corporations/me/managers", json={"character_id": 777}
        )
        assert resp.status_code == 400


async def test_director_can_manage_managers():
    # A Director (not the CEO) may designate Buyback Managers (ADR-0036).
    async with _client(DirectorEsi()) as http:
        await _login(http)
        await http.post("/api/v1/corporations")  # director registers

        add = await http.post(
            "/api/v1/corporations/me/managers", json={"character_id": 555}
        )
        assert add.status_code == 201
        listed = await http.get("/api/v1/corporations/me/managers")
        assert listed.status_code == 200
        assert 555 in {m["character_id"] for m in listed.json()}


async def test_plain_member_cannot_manage_managers():
    async with _client(MemberEsi()) as http:
        await _login(http)  # role member, not a director
        resp = await http.get("/api/v1/corporations/me/managers")
        assert resp.status_code == 403
