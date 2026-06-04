import httpx
from fastapi import Request
from pydantic import BaseModel

ESI_BASE = "https://esi.evetech.net/latest"


class CorporationInfo(BaseModel):
    name: str
    ceo_id: int
    ticker: str | None = None


class EsiClient:
    """Minimal ESI client for public character/corporation lookups."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def get_character_corporation(self, character_id: int) -> int:
        resp = await self._client.get(f"{ESI_BASE}/characters/{character_id}/")
        resp.raise_for_status()
        return resp.json()["corporation_id"]

    async def get_corporation(self, corporation_id: int) -> CorporationInfo:
        resp = await self._client.get(f"{ESI_BASE}/corporations/{corporation_id}/")
        resp.raise_for_status()
        data = resp.json()
        return CorporationInfo(
            name=data["name"], ceo_id=data["ceo_id"], ticker=data.get("ticker")
        )


def get_esi_client(request: Request) -> EsiClient:
    return EsiClient(request.app.state.http)
