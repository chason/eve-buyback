import httpx
from fastapi import Request
from pydantic import BaseModel

ESI_BASE = "https://esi.evetech.net/latest"


class CorporationInfo(BaseModel):
    name: str
    ceo_id: int
    ticker: str | None = None


class CharacterInfo(BaseModel):
    name: str
    corporation_id: int


class EsiClient:
    """Minimal ESI client for public character/corporation lookups."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def get_character(self, character_id: int) -> CharacterInfo:
        resp = await self._client.get(f"{ESI_BASE}/characters/{character_id}/")
        resp.raise_for_status()
        data = resp.json()
        return CharacterInfo(name=data["name"], corporation_id=data["corporation_id"])

    async def get_character_corporation(self, character_id: int) -> int:
        return (await self.get_character(character_id)).corporation_id

    async def get_corporation(self, corporation_id: int) -> CorporationInfo:
        resp = await self._client.get(f"{ESI_BASE}/corporations/{corporation_id}/")
        resp.raise_for_status()
        data = resp.json()
        return CorporationInfo(
            name=data["name"], ceo_id=data["ceo_id"], ticker=data.get("ticker")
        )

    async def get_character_roles(
        self, character_id: int, access_token: str
    ) -> list[str]:
        """Authenticated corp-roles lookup. Returns [] if the scope wasn't granted."""
        resp = await self._client.get(
            f"{ESI_BASE}/characters/{character_id}/roles/",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code in (401, 403):
            return []  # scope not granted — fail closed (ADR-0015)
        resp.raise_for_status()
        return resp.json().get("roles", [])


def get_esi_client(request: Request) -> EsiClient:
    return EsiClient(request.app.state.http)
