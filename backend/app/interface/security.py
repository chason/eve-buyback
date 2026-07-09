"""Session-cookie handling and the FastAPI auth dependencies. This is interface
glue: it reads/writes the signed cookie and delegates role resolution to the
application layer (ADR-0016)."""

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.application.auth import (
    AuthenticatedUser,
    SessionIdentity,
    resolve_authenticated_user,
)
from app.config import get_settings
from app.domain.app_admin import is_app_admin
from app.domain.roles import Role, role_at_least
from app.interface.deps import SessionDep

SESSION_USER_KEY = "user"


def set_session_identity(request: Request, identity: SessionIdentity) -> None:
    """Persist the stable identity in the signed session cookie."""
    request.session[SESSION_USER_KEY] = identity.model_dump()


def clear_session(request: Request) -> None:
    request.session.clear()


def get_current_identity(request: Request) -> SessionIdentity | None:
    data = request.session.get(SESSION_USER_KEY)
    return SessionIdentity(**data) if data else None


CurrentIdentity = Annotated[SessionIdentity | None, Depends(get_current_identity)]


def require_identity(identity: CurrentIdentity) -> SessionIdentity:
    if identity is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    return identity


RequireIdentity = Annotated[SessionIdentity, Depends(require_identity)]


async def get_current_user(
    identity: CurrentIdentity, session: SessionDep
) -> AuthenticatedUser | None:
    if identity is None:
        return None
    return await resolve_authenticated_user(session, identity)


CurrentUser = Annotated[AuthenticatedUser | None, Depends(get_current_user)]


def require_user(user: CurrentUser) -> AuthenticatedUser:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    return user


RequireUser = Annotated[AuthenticatedUser, Depends(require_user)]


def require_role(minimum: Role) -> Callable[[AuthenticatedUser], AuthenticatedUser]:
    """Dependency factory: require at least `minimum` (member < manager < ceo).
    The role is resolved from the DB per request, so revocation is immediate."""

    def dependency(user: RequireUser) -> AuthenticatedUser:
        if not role_at_least(user.role, minimum):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires {minimum} role",
            )
        return user

    return dependency


def is_user_app_admin(user: AuthenticatedUser) -> bool:
    """Whether the resolved user is an instance app admin (ADR-0041). Derived per request
    from the config allowlist — never stored in the session cookie, so revocation is
    immediate — and orthogonal to the corp role."""
    return is_app_admin(user.character_id, get_settings().admin_character_id_set)


def current_is_app_admin(user: CurrentUser) -> bool:
    """The app-admin flag for the current session (False when unauthenticated). Exposed on
    /me so the SPA can show the admin nav — cosmetic; `require_app_admin` is the real gate."""
    return user is not None and is_user_app_admin(user)


IsAppAdmin = Annotated[bool, Depends(current_is_app_admin)]


def require_app_admin(user: RequireUser) -> AuthenticatedUser:
    """Require the caller be an instance app admin (ADR-0041) — the operator of this hosted
    instance. Independent of the corp role hierarchy (not a super-CEO)."""
    if not is_user_app_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Requires app admin"
        )
    return user


RequireAppAdmin = Annotated[AuthenticatedUser, Depends(require_app_admin)]


def require_ceo_or_director(user: RequireUser) -> AuthenticatedUser:
    """Allow the CEO or any Director (ADR-0036). Directors administer who the Buyback
    Managers are — and sync the corp roster — even when they aren't managers themselves.
    `is_director` comes from ESI at login (ADR-0015) and rides in the session cookie."""
    if not (user.role == "ceo" or user.is_director):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires CEO or Director",
        )
    return user


RequireCeoOrDirector = Annotated[AuthenticatedUser, Depends(require_ceo_or_director)]
