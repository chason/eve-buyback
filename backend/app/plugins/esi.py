import httpx
from fastapi import Request
from pydantic import BaseModel

# Unversioned ESI base — versioning is by the `X-Compatibility-Date` header, set once on
# the shared httpx client (main.py); paths carry no `/latest/` or `/vN/` prefix.
ESI_BASE = "https://esi.evetech.net"

# ESI's bulk name-resolution endpoint accepts up to 1000 ids per request.
_NAMES_CHUNK = 1000


class CorporationMembersForbidden(Exception):
    """ESI refused the corp member list (401/403): the authorizing character lacks
    permission to read it (the membership scope and/or the required in-game role,
    ADR-0036). Transport-level; the application layer maps it to a semantic error."""


class CorporationInfo(BaseModel):
    name: str
    ceo_id: int
    ticker: str | None = None


class CharacterInfo(BaseModel):
    name: str
    corporation_id: int


class EsiClient:
    """Minimal ESI client for public character/corporation lookups.

    A plugin (outside-API gateway): it speaks HTTP to EVE's ESI and always
    returns Pydantic models, never raw JSON, to the application layer.
    """

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

    async def get_corporation_members(
        self, corporation_id: int, access_token: str
    ) -> list[int]:
        """The corp's member character ids (ADR-0036). Requires a token whose character
        has permission to read membership (the membership scope, plus any in-game role EVE
        requires); 401/403 means that character can't read the roster (raised, not
        swallowed, so the sync can explain)."""
        resp = await self._client.get(
            f"{ESI_BASE}/corporations/{corporation_id}/members/",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code in (401, 403):
            raise CorporationMembersForbidden()
        resp.raise_for_status()
        return resp.json()

    async def resolve_universe_names(self, ids: list[int]) -> dict[int, str]:
        """Resolve ids to names via the public bulk endpoint (ADR-0036), keeping only
        the characters. Chunked at ESI's 1000-id limit; an empty input is a no-op."""
        names: dict[int, str] = {}
        for start in range(0, len(ids), _NAMES_CHUNK):
            chunk = ids[start : start + _NAMES_CHUNK]
            resp = await self._client.post(f"{ESI_BASE}/universe/names/", json=chunk)
            resp.raise_for_status()
            for entry in resp.json():
                if entry.get("category") == "character":
                    names[entry["id"]] = entry["name"]
        return names


def get_esi_client(request: Request) -> EsiClient:
    return EsiClient(request.app.state.http)
