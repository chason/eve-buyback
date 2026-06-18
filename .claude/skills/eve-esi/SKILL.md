---
name: eve-esi
description: Patterns and conventions for interacting with the EVE Online ESI API, EVE SSO (OAuth2) authentication, and zkillboard killmail feeds (RedisQ) in this Python (FastAPI) + TypeScript (React) project. Use when fetching ESI data (characters, corporations, systems, ships, types, killmails), implementing EVE SSO login, processing zkillboard/RedisQ killmails, caching EVE universe data, or working with EVE-specific IDs, image URLs, and external links (zkillboard, Dotlan, EVE Tools).
---

# EVE Online ESI API Guidelines

## Purpose

Patterns for interacting with the EVE Online ESI API and zkillboard data feeds in
this project. Backend examples are Python (`httpx` + Pydantic + FastAPI); frontend
examples are TypeScript (React/Vite).

## When to Use

- Fetching data from ESI (characters, systems, ships, etc.)
- Processing killmail data from zkillboard
- Implementing ESI authentication (SSO)
- Caching EVE universe data
- Working with EVE-specific IDs and data structures

---

## Architecture: who talks to ESI

> **The Python backend owns all ESI / SSO / zkillboard traffic.** The TypeScript
> frontend talks only to *our* API.

This split matters because:

- **Secrets** — the SSO `client_secret` and access/refresh tokens must never reach
  the browser. Token exchange happens server-side.
- **CORS** — ESI does not send permissive CORS headers for every endpoint; calling
  it from the browser is unreliable.
- **Caching & rate limits** — universe data (systems, types) is shared across users
  and best cached once in the backend, not re-fetched per browser.

The frontend *does* build a few URLs directly (image CDN, external deep-links to
zkillboard/Dotlan) because those are static, public, and need no API round-trip.

---

## Key URLs and Endpoints

| Service | Base URL | Purpose |
|---------|----------|---------|
| ESI | `https://esi.evetech.net/` | Official EVE API — unversioned paths + an `X-Compatibility-Date` header (see below) |
| zkillboard RedisQ | `https://zkillredisq.stream/listen.php` | Real-time killmail feed |
| zkillboard | `https://zkillboard.com/` | Killmail browser |
| Fuzzwork | `https://www.fuzzwork.co.uk/api/` | Third-party celestial data |
| EVE SSO | `https://login.eveonline.com/` | OAuth authentication |
| EVE Images | `https://images.evetech.net/` | Character/ship/alliance icons |

> **Authoritative spec:** the OpenAPI 3 document at
> <https://esi.evetech.net/meta/openapi.json>, browsable in the interactive API explorer
> at <https://developers.eveonline.com/api-explorer>. These supersede the old Swagger 2.0
> UI (`esi.evetech.net/ui/`, `/latest/swagger.json`); `esi.evetech.net/` now redirects to
> the explorer. **Always confirm paths, scopes, and required in-game roles against this
> live spec — they change.**
>
> **Versioning = compatibility date (not URL versions).** Send an
> `X-Compatibility-Date: YYYY-MM-DD` header (or `?compatibility_date=YYYY-MM-DD`) — the
> date your integration was last validated against the ESI changelog — and use
> **unversioned** paths (`/characters/{id}/`, not `/latest/` or `/v5/`). Legacy versioned
> routes still work for now but are the old style. Pin a fixed date and bump it
> deliberately. A route flagged for removal returns a `warning: 299 …` header (ESI aims for
> ~1 year of backwards compatibility) — watch for it and migrate.
>
> **User-Agent:** ESI requires a descriptive `User-Agent` (app name + contact).
> Set it (and the compatibility-date header) once on the shared client.

---

## ESI Client Pattern (Python)

### Basic Structure

```python
import httpx

ESI_URL = "https://esi.evetech.net/"  # unversioned paths; version via the header below

# The date your ESI integration was last reviewed against the changelog. ESI serves the
# API behaviour as it was on this date, so behaviour only changes when *you* bump it. The
# spec's `CompatibilityDate` enum lists the valid dates (the explorer shows them); newer
# dates opt into newer behaviour. 2020-01-01 is the conservative baseline.
ESI_COMPATIBILITY_DATE = "2020-01-01"


class EsiClient:
    """Thin async wrapper around a shared httpx client."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=ESI_URL,
            timeout=httpx.Timeout(30.0),
            headers={
                "User-Agent": "buyback/1.0 (you@example.com)",
                "X-Compatibility-Date": ESI_COMPATIBILITY_DATE,
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str) -> object:
        resp = await self._client.get(path)
        resp.raise_for_status()  # non-2xx -> httpx.HTTPStatusError
        return resp.json()
```

Construct one `EsiClient` per process and share it (e.g. store it on FastAPI
`app.state` and close it in the lifespan shutdown). Don't create a client per
request.

### Common ESI Endpoints

```python
async def get_system(self, system_id: int) -> System:
    data = await self._get(f"universe/systems/{system_id}/")
    return System.model_validate(data)

# Get ship/item group
async def get_type_group_id(self, type_id: int) -> int:
    data = await self._get(f"universe/types/{type_id}/")
    return data["group_id"]

# Resolve IDs -> names (POST endpoint, body is a JSON array of IDs)
async def resolve_names(self, ids: list[int]) -> dict[int, str]:
    resp = await self._client.post("universe/names/", json=ids)
    resp.raise_for_status()
    return {item["id"]: item["name"] for item in resp.json()}

# Character affiliation (POST endpoint) -> (corp_id, alliance_id)
async def get_character_affiliation(
    self, character_id: int
) -> tuple[int, int | None]:
    resp = await self._client.post("characters/affiliation/", json=[character_id])
    resp.raise_for_status()
    a = resp.json()[0]
    return a["corporation_id"], a.get("alliance_id")
```

---

## zkillboard RedisQ (Python)

### Listener Pattern

```python
import httpx

REDISQ_URL = "https://zkillredisq.stream/listen.php"


class RedisQListener:
    def __init__(self, queue_id: str) -> None:
        # Each listener needs a unique, stable queue ID.
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0))
        self._params = {"queueID": queue_id}

    async def listen(self) -> "ZkData | None":
        # Long-poll: the server holds the request open until a kill or ~10s timeout.
        resp = await self._client.get(REDISQ_URL, params=self._params)
        resp.raise_for_status()
        package = resp.json().get("package")
        if package is None:
            return None  # no new killmails this cycle
        return ZkData.model_validate(package)

    async def aclose(self) -> None:
        await self._client.aclose()
```

### Main Loop Pattern

```python
import asyncio
import logging

log = logging.getLogger(__name__)


async def run_redisq(listener: RedisQListener) -> None:
    while True:
        try:
            zk = await listener.listen()
            if zk is None:
                await asyncio.sleep(1)  # idle, poll again
                continue
            log.info("[Kill %s] received", zk.kill_id)
            await process_killmail(zk)
            await asyncio.sleep(1)
        except Exception:
            log.exception("RedisQ error")
            await asyncio.sleep(5)  # back off on error
```

Run this as a background task from the FastAPI lifespan (`asyncio.create_task`),
and cancel it on shutdown.

---

## Data Models

### Python (Pydantic)

```python
from pydantic import BaseModel, ConfigDict, Field


class Position(BaseModel):
    x: float
    y: float
    z: float


class Victim(BaseModel):
    ship_type_id: int
    character_id: int | None = None
    corporation_id: int | None = None
    alliance_id: int | None = None
    position: Position | None = None


class Attacker(BaseModel):
    ship_type_id: int | None = None
    weapon_type_id: int | None = None
    character_id: int | None = None
    corporation_id: int | None = None
    alliance_id: int | None = None
    final_blow: bool = False


class KillmailData(BaseModel):
    killmail_id: int
    killmail_time: str  # RFC3339
    solar_system_id: int
    victim: Victim
    attackers: list[Attacker]


class ZkbMeta(BaseModel):
    total_value: float
    location_id: int | None = None
    esi: str  # URL to the full ESI killmail


class ZkData(BaseModel):
    # RedisQ sends "killID"; expose it as kill_id.
    model_config = ConfigDict(populate_by_name=True)
    kill_id: int = Field(alias="killID")
    killmail: KillmailData
    zkb: ZkbMeta


class System(BaseModel):
    # Project-composed/enriched model (region fields are joined in, not raw ESI).
    id: int
    name: str
    security_status: float
    region_id: int
    region: str
    x: float
    y: float
    z: float
```

### TypeScript (frontend types)

Mirror only the fields the UI actually renders. These describe the JSON your
**backend** returns, so keep them in sync with the Pydantic models above.

```typescript
export interface Position { x: number; y: number; z: number }

export interface Victim {
  shipTypeId: number
  characterId?: number
  corporationId?: number
  allianceId?: number
  position?: Position
}

export interface Attacker {
  shipTypeId?: number
  weaponTypeId?: number
  characterId?: number
  corporationId?: number
  allianceId?: number
  finalBlow: boolean
}

export interface Killmail {
  killId: number
  killmailTime: string // RFC3339
  solarSystemId: number
  totalValue: number
  victim: Victim
  attackers: Attacker[]
}

export interface System {
  id: number
  name: string
  securityStatus: number
  regionId: number
  region: string
}
```

> If the backend serializes `snake_case`, either expose camelCase via FastAPI
> response models / `alias_generator`, or convert at the fetch boundary. Pick one
> convention and apply it everywhere.

---

## Caching Strategy (Python)

EVE universe data (systems, types, regions) is effectively immutable, so cache it
aggressively and persist it. Killmail-derived/runtime data uses a TTL cache.

### Persistent cache for universe data

```python
import asyncio


class SystemRepository:
    """Read-through cache: memory -> disk -> ESI."""

    def __init__(self, esi: EsiClient, store: dict[int, System]) -> None:
        self._esi = esi
        self._systems = store        # loaded from disk at startup
        self._lock = asyncio.Lock()

    async def get(self, system_id: int) -> System | None:
        cached = self._systems.get(system_id)
        if cached is not None:
            return cached
        async with self._lock:  # avoid a stampede on the same id
            if (cached := self._systems.get(system_id)) is not None:
                return cached
            try:
                system = await self._esi.get_system(system_id)
            except httpx.HTTPError:
                log.warning("Failed to fetch system %s", system_id, exc_info=True)
                return None
            self._systems[system_id] = system
            save_systems(self._systems)  # persist to disk
            return system
```

### Time-based in-memory cache

Use `cachetools.TTLCache` (sync access) or `aiocache` (async-native) for data that
should expire — e.g. derived celestial info:

```python
from cachetools import TTLCache

celestial_cache: TTLCache[int, Celestial] = TTLCache(maxsize=10_000, ttl=3600)

cached = celestial_cache.get(system_id)
if cached is not None:
    return cached

celestial = await fetch_celestial(system_id)
celestial_cache[system_id] = celestial
return celestial
```

---

## EVE-Specific Knowledge

### Important IDs

| Type | Example IDs | Notes |
|------|-------------|-------|
| Ship Groups | 485 (Dread), 659 (Super), 30 (Titan) | Used for filtering |
| Regions | 10000030 (Devoid), 10000012 (Curse) | Universe IDs |
| Systems | 30000142 (Jita), 30002086 (Turnur) | Solar system IDs |

See [resources/ids.md](resources/ids.md) for the full lookup tables.

### Ship Group Priorities

Backend (Python) — order matters; lower index = higher priority:

```python
SHIP_GROUP_PRIORITY: tuple[int, ...] = (
    30,    # Titan
    659,   # Supercarrier
    4594,  # Lancer
    485,   # Dreadnought
    1538,  # FAX
    547,   # Carrier
    883,   # Capital Industrial Ship
    902,   # Jump Freighter
    513,   # Freighter
)
```

### Image URLs (frontend)

Static, public CDN URLs — build them directly in TypeScript, no API call needed:

```typescript
const IMAGES = "https://images.evetech.net"

export const allianceIcon = (id: number, size = 64) =>
  `${IMAGES}/alliances/${id}/logo?size=${size}`
export const corpIcon = (id: number, size = 64) =>
  `${IMAGES}/corporations/${id}/logo?size=${size}`
export const shipIcon = (id: number, size = 64) =>
  `${IMAGES}/types/${id}/icon?size=${size}`
export const characterPortrait = (id: number, size = 64) =>
  `${IMAGES}/characters/${id}/portrait?size=${size}`
```

### External Links (frontend)

```typescript
// zkillboard
export const zkbKill = (id: number) => `https://zkillboard.com/kill/${id}/`
export const zkbCharacter = (id: number) => `https://zkillboard.com/character/${id}/`
export const zkbCorp = (id: number) => `https://zkillboard.com/corporation/${id}/`

// Dotlan
export const dotlanSystem = (id: number) => `http://evemaps.dotlan.net/system/${id}`
export const dotlanRegion = (id: number) => `http://evemaps.dotlan.net/region/${id}`

// EVE Tools battle report
export const brLink = (systemId: number, timestamp: string) =>
  `https://br.evetools.org/related/${systemId}/${timestamp}`
```

---

## SSO Authentication (Python)

OAuth2 authorization-code flow. The `client_secret` and tokens stay server-side;
the browser only ever sees a redirect to `login.eveonline.com` and, afterward, a
session cookie / your own JWT.

```python
import httpx
from pydantic import BaseModel

ESI_TOKEN_URL = "https://login.eveonline.com/v2/oauth/token"
ESI_VERIFY_URL = "https://login.eveonline.com/oauth/verify"


class EveAuthToken(BaseModel):
    character_id: int
    character_name: str
    access_token: str
    refresh_token: str


async def exchange_code_for_token(
    code: str, client_id: str, client_secret: str
) -> EveAuthToken:
    async with httpx.AsyncClient() as client:
        # 1. Exchange the authorization code for tokens (HTTP Basic auth).
        token_resp = await client.post(
            ESI_TOKEN_URL,
            data={"grant_type": "authorization_code", "code": code},
            auth=(client_id, client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        token_resp.raise_for_status()
        tokens = token_resp.json()
        access_token = tokens["access_token"]
        refresh_token = tokens["refresh_token"]

        # 2. Verify the token and read character identity.
        verify_resp = await client.get(
            ESI_VERIFY_URL, headers={"Authorization": f"Bearer {access_token}"}
        )
        verify_resp.raise_for_status()
        info = verify_resp.json()

    return EveAuthToken(
        character_id=info["CharacterID"],
        character_name=info["CharacterName"],
        access_token=access_token,
        refresh_token=refresh_token,
    )
```

> SSO v2 returns a signed JWT access token; validating it against the published
> JWKS is the modern alternative to `/oauth/verify`. The verify endpoint shown
> above still works and is simpler to start with.

### FastAPI callback route

```python
from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.get("/auth/callback")
async def auth_callback(request: Request, code: str, state: str) -> dict:
    # Validate `state` against the value you stored before the redirect (CSRF).
    if not state_is_valid(request, state):
        raise HTTPException(status_code=400, detail="invalid state")
    token = await exchange_code_for_token(code, settings.client_id, settings.client_secret)
    # Persist token server-side; hand the browser a session, not the EVE tokens.
    await save_token(token)
    return {"character_id": token.character_id, "character_name": token.character_name}
```

### Authenticated requests

```python
async def get_contacts(
    self, entity_id: int, token: str, kind: str = "characters"
) -> list[StandingContact]:
    # kind is one of "characters", "corporations", "alliances"
    resp = await self._client.get(
        f"{kind}/{entity_id}/contacts/",
        headers={"Authorization": f"Bearer {token}"},
    )
    resp.raise_for_status()
    return [StandingContact.model_validate(c) for c in resp.json()]
```

---

## Frontend → Backend API Client (TypeScript)

The browser fetches EVE data from *your* API, not from ESI. Configure the base URL
via env (`VITE_API_BASE_URL`); never hardcode it.

```typescript
const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api"

async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: "include", // send the session cookie
  })
  if (!res.ok) throw new Error(`API ${res.status}: ${res.statusText}`)
  return res.json() as Promise<T>
}

export const getSystem = (id: number) => apiGet<System>(`/systems/${id}`)
export const getKillmail = (id: number) => apiGet<Killmail>(`/killmails/${id}`)
```

---

## Reference Files

- [resources/endpoints.md](resources/endpoints.md) - Complete ESI endpoint reference
- [resources/ids.md](resources/ids.md) - Common EVE IDs lookup table
