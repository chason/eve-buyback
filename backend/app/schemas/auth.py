from pydantic import BaseModel

from app.domain.roles import Role


class LoginUrlResponse(BaseModel):
    authorization_url: str
    state: str


class LoginRequest(BaseModel):
    code: str
    state: str


class SessionUser(BaseModel):
    """The authenticated user returned by /me and /login (API DTO)."""

    character_id: int
    character_name: str
    corporation_id: int
    corporation_name: str
    role: Role
    # EVE Director role — enables corp registration (ADR-0015).
    is_director: bool = False
    # Whether this corporation is a registered tenant.
    corporation_registered: bool = False
    # Whether this session can open a matched contract in EVE (ADR-0038): true once the
    # session was opened with the open-window scope (i.e. after that feature shipped).
    can_open_contract: bool = False
