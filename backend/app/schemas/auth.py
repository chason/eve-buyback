from typing import Literal

from pydantic import BaseModel

Role = Literal["member", "manager", "ceo"]


class LoginUrlResponse(BaseModel):
    authorization_url: str
    state: str


class LoginRequest(BaseModel):
    code: str
    state: str


class SessionUser(BaseModel):
    """The authenticated user as stored in the session and returned by /me."""

    character_id: int
    character_name: str
    corporation_id: int
    corporation_name: str
    role: Role
    # EVE Director role — enables corp registration (ADR-0015).
    is_director: bool = False
    # Whether this corporation is a registered tenant.
    corporation_registered: bool = False
