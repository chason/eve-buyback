# Common EVE IDs

A lookup table for the IDs referenced throughout `../SKILL.md`, plus the ID-range
conventions that let you classify an unknown ID at a glance.

> **Source of truth:** the EVE Static Data Export (SDE) and Fuzzwork
> (<https://www.fuzzwork.co.uk/>). At runtime, resolve any ID → name with
> `POST universe/names/` (see `endpoints.md`). Treat the tables below as a curated
> cache of the values this project hardcodes, not an exhaustive list.

## ID ranges (classify an unknown ID)

EVE assigns IDs in fixed numeric bands, so the magnitude tells you the type:

| Range | Entity |
|-------|--------|
| `< 10,000` | Type IDs (ships, modules, items) and group/category IDs live in the low ranges |
| `1,000,000 – 2,000,000` | NPC corporations |
| `3,000,000 – 4,000,000` | NPC characters (agents) |
| `10,000,000 – 10,999,999` | Regions (k-space) |
| `11,000,000 +` | Regions (w-space / abyssal) |
| `20,000,000 +` | Constellations |
| `30,000,000 +` | Solar systems (k-space; `31,000,000+` = wormhole systems) |
| `40,000,000 +` | Celestials (planets, moons, asteroid belts) |
| `60,000,000 +` | Stations (NPC) |
| `90,000,000 +` and `100,000,000 +` | Player characters |
| `98,000,000 – 98,999,999` | Player corporations |
| `99,000,000 – 99,999,999` | Player alliances |

These bands are stable and are why the SKILL.md models can use `u32`/`int` IDs and
still tell a system from an alliance.

## Ship groups

`group_id` values, in the priority order used by `SHIP_GROUP_PRIORITY` in
SKILL.md (highest priority first). A type's group comes from
`GET universe/types/{type_id}/` → `group_id`.

| Priority | Group ID | Name | Example hull |
|----------|----------|------|--------------|
| 1 | 30 | Titan | Avatar, Erebus |
| 2 | 659 | Supercarrier | Nyx, Aeon |
| 3 | 4594 | Lancer Dreadnought | Bane, Karura |
| 4 | 485 | Dreadnought | Naglfar, Revelation |
| 5 | 1538 | Force Auxiliary (FAX) | Apostle, Minokawa |
| 6 | 547 | Carrier | Thanatos, Archon |
| 7 | 883 | Capital Industrial Ship | Rorqual |
| 8 | 902 | Jump Freighter | Anshar, Rhea |
| 9 | 513 | Freighter | Charon, Obelisk |

> Not in the priority list but commonly needed for sub-cap filtering: resolve these
> from the SDE rather than hardcoding if you can — `groups/{id}/` confirms the name.

## Regions

| Region ID | Name | Notes |
|-----------|------|-------|
| 10000002 | The Forge | Contains Jita — highest trade volume in EVE |
| 10000012 | Curse | Nullsec (NPC), referenced in SKILL.md |
| 10000030 | Devoid | Lowsec/highsec border, referenced in SKILL.md |

## Solar systems

| System ID | Name | Region | Notes |
|-----------|------|--------|-------|
| 30000142 | Jita | The Forge | Primary trade hub (Jita 4-4) |
| 30002086 | Turnur | _resolve at runtime_ | Referenced in SKILL.md; confirm region via system → constellation → region |

> Only IDs explicitly used or named in SKILL.md are listed as project constants.
> Add rows here as you hardcode more — and prefer runtime resolution for anything
> the user supplies.
