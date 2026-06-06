from urllib.parse import urlencode

import httpx
from fastapi import Request
from pydantic import BaseModel

from app.config import Settings, get_settings

EVE_AUTHORIZE_URL = "https://login.eveonline.com/v2/oauth/authorize"
EVE_TOKEN_URL = "https://login.eveonline.com/v2/oauth/token"
EVE_VERIFY_URL = "https://login.eveonline.com/oauth/verify"


class OAuthToken(BaseModel):
    access_token: str
    refresh_token: str | None = None


class VerifiedCharacter(BaseModel):
    character_id: int
    name: str


class EveSsoClient:
    """EVE SSO OAuth2 client. The backend is the confidential client (ADR-0004).

    A plugin (outside-API gateway): pure transport to EVE SSO. PKCE/state values
    are generated in the domain layer and passed in; this client only builds URLs
    and exchanges/verifies tokens, returning Pydantic models.
    """

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._client = client

    @property
    def configured(self) -> bool:
        return bool(self._settings.eve_client_id and self._settings.eve_client_secret)

    def build_authorize_url(self, *, state: str, code_challenge: str) -> str:
        params = {
            "response_type": "code",
            "redirect_uri": self._settings.eve_redirect_uri,
            "client_id": self._settings.eve_client_id,
            "scope": self._settings.eve_scopes,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{EVE_AUTHORIZE_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str, code_verifier: str) -> OAuthToken:
        resp = await self._client.post(
            EVE_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": code_verifier,
            },
            auth=(self._settings.eve_client_id, self._settings.eve_client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        data = resp.json()
        return OAuthToken(
            access_token=data["access_token"], refresh_token=data.get("refresh_token")
        )

    async def verify_token(self, access_token: str) -> VerifiedCharacter:
        resp = await self._client.get(
            EVE_VERIFY_URL, headers={"Authorization": f"Bearer {access_token}"}
        )
        resp.raise_for_status()
        data = resp.json()
        return VerifiedCharacter(
            character_id=data["CharacterID"], name=data["CharacterName"]
        )


def get_sso_client(request: Request) -> EveSsoClient:
    return EveSsoClient(get_settings(), request.app.state.http)
