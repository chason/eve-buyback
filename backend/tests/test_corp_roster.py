"""Corp-roster sync + manager-designation search (ADR-0036)."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.plugins.esi import (
    CharacterInfo,
    CorporationInfo,
    CorporationMembersForbidden,
    get_esi_client,
)
from app.plugins.sso import OAuthToken, VerifiedCharacter, get_sso_client

CHAR_ID = 12345
CORP_ID = 98000001


class FakeSso:
    configured = True

    def build_authorize_url(
        self, *, state: str, code_challenge: str, scopes: str | None = None
    ) -> str:
        return f"https://login.eveonline.com/v2/oauth/authorize?state={state}"

    async def exchange_code(self, code: str, code_verifier: str) -> OAuthToken:
        return OAuthToken(access_token="fake-access-token")

    async def verify_token(self, access_token: str) -> VerifiedCharacter:
        return VerifiedCharacter(character_id=CHAR_ID, name="Boss")


class CeoEsi:
    ceo_id = CHAR_ID
    roles: list[str] = []
    members = [101, 102, 103]
    names = {101: "Alice", 102: "Bob", 103: "Albert"}
    forbid_members = False

    async def get_character(self, character_id: int) -> CharacterInfo:
        return CharacterInfo(name="Grunt", corporation_id=CORP_ID)

    async def get_character_corporation(self, character_id: int) -> int:
        return CORP_ID

    async def get_corporation(self, corporation_id: int) -> CorporationInfo:
        return CorporationInfo(name="Test Corp", ceo_id=self.ceo_id, ticker="T")

    async def get_character_roles(
        self, character_id: int, access_token: str
    ) -> list[str]:
        return self.roles

    async def get_corporation_members(
        self, corporation_id: int, access_token: str
    ) -> list[int]:
        if self.forbid_members:
            raise CorporationMembersForbidden()
        return self.members

    async def resolve_universe_names(self, ids: list[int]) -> dict[int, str]:
        return {i: self.names[i] for i in ids if i in self.names}


class DirectorEsi(CeoEsi):
    ceo_id = 99999
    roles = ["Director"]


class MemberEsi(CeoEsi):
    ceo_id = 99999


class ForbiddenEsi(CeoEsi):
    forbid_members = True


def _client(esi: CeoEsi) -> AsyncClient:
    app.dependency_overrides[get_sso_client] = lambda: FakeSso()
    app.dependency_overrides[get_esi_client] = lambda: esi
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
    resp = await http.post(
        "/api/v1/auth/session", json={"code": "auth-code", "state": state}
    )
    assert resp.status_code == 200
    return resp.json()


async def _sync_roster(http: AsyncClient) -> dict:
    begin = await http.post("/api/v1/corporations/me/roster/sync")
    assert begin.status_code == 200
    state = begin.json()["state"]
    assert state.startswith("roster.")
    done = await http.post(
        "/api/v1/corporations/me/roster/sync/session",
        json={"code": "roster-code", "state": state},
    )
    return done


async def test_sync_then_search_filters_to_members():
    async with _client(CeoEsi()) as http:
        await _login(http)
        await http.post("/api/v1/corporations")

        done = await _sync_roster(http)
        assert done.status_code == 200
        assert done.json() == {
            "synced": True,
            "synced_at": done.json()["synced_at"],
            "member_count": 3,
        }

        status = await http.get("/api/v1/corporations/me/roster")
        assert status.json()["synced"] is True
        assert status.json()["member_count"] == 3

        # ILIKE %al% matches Alice + Albert (case-insensitive), ordered by name.
        results = await http.get("/api/v1/corporations/me/roster/members?q=al")
        assert results.status_code == 200
        assert [m["name"] for m in results.json()] == ["Albert", "Alice"]
        assert {m["character_id"] for m in results.json()} == {101, 103}


async def test_search_is_empty_before_sync():
    async with _client(CeoEsi()) as http:
        await _login(http)
        await http.post("/api/v1/corporations")

        status = await http.get("/api/v1/corporations/me/roster")
        assert status.json() == {"synced": False, "synced_at": None, "member_count": 0}

        results = await http.get("/api/v1/corporations/me/roster/members?q=al")
        assert results.status_code == 200
        assert results.json() == []


async def test_resync_replaces_the_snapshot():
    esi = CeoEsi()
    async with _client(esi) as http:
        await _login(http)
        await http.post("/api/v1/corporations")
        await _sync_roster(http)

        # Membership changed: Bob left, Carol joined.
        esi.members = [101, 104]
        esi.names = {101: "Alice", 104: "Carol"}
        done = await _sync_roster(http)
        assert done.json()["member_count"] == 2

        gone = await http.get("/api/v1/corporations/me/roster/members?q=bob")
        assert gone.json() == []
        carol = await http.get("/api/v1/corporations/me/roster/members?q=carol")
        assert [m["name"] for m in carol.json()] == ["Carol"]


async def test_sync_forbidden_surfaces_403():
    async with _client(ForbiddenEsi()) as http:
        await _login(http)
        await http.post("/api/v1/corporations")
        done = await _sync_roster(http)
        assert done.status_code == 403


async def test_director_can_sync_roster():
    async with _client(DirectorEsi()) as http:
        await _login(http)
        await http.post("/api/v1/corporations")  # director registers
        done = await _sync_roster(http)
        assert done.status_code == 200
        assert done.json()["member_count"] == 3


async def test_member_cannot_access_roster():
    async with _client(MemberEsi()) as http:
        await _login(http)  # plain member: role member, not a director
        assert (await http.get("/api/v1/corporations/me/roster")).status_code == 403
        assert (
            await http.post("/api/v1/corporations/me/roster/sync")
        ).status_code == 403
        assert (
            await http.get("/api/v1/corporations/me/roster/members?q=al")
        ).status_code == 403
