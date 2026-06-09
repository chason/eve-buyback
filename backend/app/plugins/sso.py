from urllib.parse import urlencode

import httpx
from fastapi import Request
from pydantic import BaseModel

from app.config import Settings, get_settings

EVE_AUTHORIZE_URL = "https://login.eveonline.com/v2/oauth/authorize"
EVE_TOKEN_URL = "https://login.eveonline.com/v2/oauth/token"
EVE_VERIFY_URL = "https://login.eveonline.com/oauth/verify"
EVE_REVOKE_URL = "https://login.eveonline.com/v2/oauth/revoke"


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

    def build_authorize_url(
        self, *, state: str, code_challenge: str, scopes: str | None = None
    ) -> str:
        """Build the SSO authorize URL. `scopes` overrides the default login scopes —
        used by the separate structure-access flow (ADR-0029)."""
        params = {
            "response_type": "code",
            "redirect_uri": self._settings.eve_redirect_uri,
            "client_id": self._settings.eve_client_id,
            "scope": scopes or self._settings.eve_scopes,
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

    async def refresh_access_token(self, refresh_token: str) -> OAuthToken:
        """Exchange a refresh token for a fresh access token (ADR-0029). EVE may
        rotate the refresh token, so the response's `refresh_token` (when present)
        should be re-persisted by the caller. A revoked grant returns HTTP 400
        (`invalid_grant`) → `raise_for_status` raises `HTTPStatusError`."""
        resp = await self._client.post(
            EVE_TOKEN_URL,
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            auth=(self._settings.eve_client_id, self._settings.eve_client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        data = resp.json()
        return OAuthToken(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
        )

    async def revoke_refresh_token(self, refresh_token: str) -> None:
        """Revoke a refresh token at EVE SSO (RFC 7009 token revocation), so the
        grant is actually terminated rather than merely forgotten by us. Idempotent:
        EVE returns 200 with an empty body even for an unknown/already-revoked token."""
        resp = await self._client.post(
            EVE_REVOKE_URL,
            data={"token": refresh_token, "token_type_hint": "refresh_token"},
            auth=(self._settings.eve_client_id, self._settings.eve_client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()

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
