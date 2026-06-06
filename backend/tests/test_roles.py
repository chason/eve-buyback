"""Proves ADR-0016: the app role is resolved from the DB on every request, so a
revoked manager assignment is enforced on the caller's next request without any
re-login or cookie refresh."""

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.auth.sso import OAuthToken, VerifiedCharacter, get_sso_client
from app.db import SessionLocal
from app.eve.esi import CharacterInfo, CorporationInfo, get_esi_client
from app.main import app
from app.models import ManagerAssignment

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


class DirectorEsi:
    """A Director (not CEO); registering auto-grants the manager assignment."""

    async def get_character(self, character_id: int) -> CharacterInfo:
        return CharacterInfo(name="Boss", corporation_id=CORP_ID)

    async def get_character_corporation(self, character_id: int) -> int:
        return CORP_ID

    async def get_corporation(self, corporation_id: int) -> CorporationInfo:
        return CorporationInfo(name="Test Corp", ceo_id=99999, ticker="T")

    async def get_character_roles(self, character_id: int, access_token: str) -> list[str]:
        return ["Director"]


async def test_manager_revocation_takes_effect_without_relogin():
    app.dependency_overrides[get_sso_client] = lambda: FakeSso()
    app.dependency_overrides[get_esi_client] = lambda: DirectorEsi()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-Buyback-CSRF": "1"},
        ) as http:
            state = (await http.get("/api/v1/auth/login-url")).json()["state"]
            login = await http.post(
                "/api/v1/auth/login", json={"code": "auth-code", "state": state}
            )
            assert login.json()["role"] == "member"

            # Registering as a Director auto-grants the manager assignment.
            assert (await http.post("/api/v1/corporations")).status_code == 201
            assert (await http.get("/api/v1/auth/me")).json()["role"] == "manager"

            # Revoke the assignment directly in the DB (same cookie, no re-login).
            async with SessionLocal() as db:
                await db.execute(
                    delete(ManagerAssignment).where(
                        ManagerAssignment.character_id == CHAR_ID
                    )
                )
                await db.commit()

            # The very next request resolves the role fresh from the DB → member.
            assert (await http.get("/api/v1/auth/me")).json()["role"] == "member"
    finally:
        app.dependency_overrides.clear()
