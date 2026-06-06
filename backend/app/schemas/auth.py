from typing import Literal

from pydantic import BaseModel

Role = Literal["member", "manager", "ceo"]


class LoginUrlResponse(BaseModel):
    authorization_url: str
    state: str


class LoginRequest(BaseModel):
    code: str
    state: str


class SessionIdentity(BaseModel):
    """Stable identity stored in the signed session cookie (ADR-0016).

    Holds only facts established at login from EVE SSO/ESI. The mutable app role
    and registration status are NOT stored here — they are resolved from the
    database on every request so changes (e.g. manager revocation) take effect
    immediately. `is_ceo`/`is_director` come from ESI at login and cannot be
    re-derived per request without an EVE token, so they live in the cookie.
    """

    character_id: int
    character_name: str
    corporation_id: int
    corporation_name: str
    is_director: bool = False
    is_ceo: bool = False


class SessionUser(BaseModel):
    """The authenticated user returned by /me — identity plus DB-resolved role."""

    character_id: int
    character_name: str
    corporation_id: int
    corporation_name: str
    role: Role
    # EVE Director role — enables corp registration (ADR-0015).
    is_director: bool = False
    # Whether this corporation is a registered tenant.
    corporation_registered: bool = False
