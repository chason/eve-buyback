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
