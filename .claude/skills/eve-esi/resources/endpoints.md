# ESI Endpoint Reference

Endpoints this project uses, plus the closely-related ones needed to enrich the
data models in `../SKILL.md`. **Authoritative, always-current source:** the OpenAPI 3
spec at <https://esi.evetech.net/meta/openapi.json>, browsable in the interactive API
explorer at <https://developers.eveonline.com/api-explorer>. (These superseded the old
Swagger 2.0 UI at `esi.evetech.net/ui/` + `/latest/swagger.json`; `esi.evetech.net/` now
redirects to the explorer.) The paths/scopes below are a convenience snapshot — confirm
against the live spec, especially **required in-game roles**, which it documents per route.

## Conventions

- **Base URL + versioning:** `https://esi.evetech.net/` with **unversioned** paths (e.g.
  `universe/systems/{id}/`). ESI versions by **compatibility date**: send an
  `X-Compatibility-Date: YYYY-MM-DD` header (or `?compatibility_date=YYYY-MM-DD`) — the
  date you last validated against the changelog. Legacy `/latest/` and `/vN/` routes still
  work for the foreseeable future but are the old style; prefer the header on the shared
  client (see SKILL.md). A route flagged for removal returns a `warning: 299 …` header
  (~1 year of backwards compatibility) — watch for it and migrate.
- **Datasource:** defaults to Tranquility (live). Append `?datasource=tranquility`
  explicitly if you want to be unambiguous.
- **User-Agent:** required — send app name + contact, e.g.
  `buyback/1.0 (you@example.com)`. Set it once on the shared `httpx.AsyncClient`.
- **Caching:** responses carry an `Expires` header. Universe data (systems, types,
  groups, regions) is effectively immutable — cache it hard (see SKILL.md).
- **Error limit:** ESI enforces a sliding error budget. Watch
  `X-Esi-Error-Limit-Remain` / `X-Esi-Error-Limit-Reset`; back off when remaining
  is low. (The RedisQ loop in SKILL.md already sleeps 5s on error.)
- **ID resolution:** most endpoints return numeric IDs, not names. Resolve with
  `POST universe/names/` (see below).

## Public endpoints (no auth)

| Method | Path | Returns / Notes | Used by |
|--------|------|-----------------|---------|
| GET | `universe/systems/{system_id}/` | `name`, `security_status`, `constellation_id`, `position{x,y,z}`, `planets`, `stargates`, `star_id` | `EsiClient.get_system` |
| GET | `universe/constellations/{constellation_id}/` | `name`, `region_id`, member `systems` | enrich `System.region_id` (system → constellation → region) |
| GET | `universe/regions/{region_id}/` | `name`, member `constellations` | enrich `System.region` name |
| GET | `universe/types/{type_id}/` | `name`, `group_id`, `description`, attributes | `EsiClient.get_type_group_id` |
| GET | `universe/groups/{group_id}/` | `name`, `category_id`, member `types` | map a ship to its group (Titan/Dread/…) |
| POST | `universe/names/` | body: `[id, …]` (max 1000). Returns `[{category, id, name}]` | `EsiClient.resolve_names` |
| POST | `universe/ids/` | body: `["name", …]`. Reverse of `names/` (name → id) | optional lookups |
| POST | `characters/affiliation/` | body: `[character_id, …]`. Returns `[{character_id, corporation_id, alliance_id?, faction_id?}]` | `EsiClient.get_character_affiliation` |
| GET | `characters/{character_id}/` | `name`, `corporation_id`, `birthday`, … | label victims/attackers |
| GET | `corporations/{corporation_id}/` | `name`, `ticker`, `alliance_id?`, `member_count` | label corps |
| GET | `alliances/{alliance_id}/` | `name`, `ticker`, `creator_id`, … | label alliances |
| GET | `killmails/{killmail_id}/{killmail_hash}/` | Full canonical killmail. The hash comes from `ZkbMeta.esi` (the URL zkb hands you) | resolve a RedisQ kill to authoritative ESI data |

> **Composing the `System` model:** ESI's `universe/systems/{id}/` gives
> `constellation_id` and `position`, but **not** the region. To fill the
> `region_id` / `region` fields in SKILL.md's `System`, follow
> system → `universe/constellations/{constellation_id}/` (→ `region_id`) →
> `universe/regions/{region_id}/` (→ `name`). Cache both hops.

## Authenticated endpoints (Bearer token)

Require an SSO access token in `Authorization: Bearer <token>` and the matching
scope granted at login.

| Method | Path | Scope | Used by |
|--------|------|-------|---------|
| GET | `characters/{character_id}/contacts/` | `esi-characters.read_contacts.v1` | `EsiClient.get_contacts(kind="characters")` |
| GET | `corporations/{corporation_id}/contacts/` | `esi-corporations.read_contacts.v1` | `get_contacts(kind="corporations")` |
| GET | `alliances/{alliance_id}/contacts/` | `esi-alliances.read_contacts.v1` | `get_contacts(kind="alliances")` |

Contact objects: `{contact_id, contact_type, standing, label_ids?}` where
`contact_type` is `character` / `corporation` / `alliance` / `faction` and
`standing` is `-10.0 … 10.0`.

## EVE SSO (OAuth2)

Host: `https://login.eveonline.com`

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/v2/oauth/authorize` | Send the user here to log in. Params: `response_type=code`, `redirect_uri`, `client_id`, `scope` (space-separated), `state` (CSRF) |
| POST | `/v2/oauth/token` | Exchange `code` → tokens, or `refresh_token` → new access token. HTTP Basic auth with `client_id:client_secret` |
| GET | `/oauth/verify` | Bearer the access token → `{CharacterID, CharacterName, ExpiresOn, Scopes, …}` |
| GET | `/.well-known/oauth-authorization-server` | Discovery doc (issuer, JWKS URI) for validating the v2 JWT access token locally |

Token refresh body (`POST /v2/oauth/token`):
`grant_type=refresh_token&refresh_token=<token>` with the same Basic auth.

## zkillboard

| Method | URL | Purpose |
|--------|-----|---------|
| GET | `https://zkillredisq.stream/listen.php?queueID=<unique_id>` | RedisQ long-poll. Returns `{"package": null}` when idle, or `{"package": {killID, killmail{…}, zkb{…}}}` on a new kill |

- `queueID` must be unique and stable per listener — reusing one elsewhere steals
  your kills.
- `zkb.esi` is the deep link to `killmails/{id}/{hash}/` for the full mail.
- zkillboard also has a REST history API at `https://zkillboard.com/api/` (heavily
  cached; respect its `User-Agent` and rate rules) if you need backfill rather than
  the live feed.
