"""Shared constants/helpers for the ESI gateways (`esi.py`, `esi_market.py`).

Both plugins speak HTTP to the same API, so the base URL and a couple of response
idioms live here rather than being duplicated (architecture audit #6, #22).
"""

import httpx

# Unversioned ESI base — versioning is by the `X-Compatibility-Date` header set once on
# the shared httpx client (main.py, ADR-style date versioning); paths carry no `/latest/`
# or `/vN/` prefix.
ESI_BASE = "https://esi.evetech.net"


def scope_missing(resp: httpx.Response) -> bool:
    """Whether ESI refused the call for lack of authorization — 401 (no/expired token) or
    403 (token lacks the required scope or in-game role). Callers map this to an empty
    result or a typed error, depending on the endpoint. (404 — a missing/inaccessible
    entity — is a *different* concern and is checked separately where it applies.)"""
    return resp.status_code in (401, 403)
