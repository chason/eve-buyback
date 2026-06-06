from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select

from app.deps import SessionDep
from app.models import Corporation, ManagerAssignment
from app.schemas.auth import Role, SessionIdentity, SessionUser

SESSION_USER_KEY = "user"

ROLE_ORDER: dict[Role, int] = {"member": 0, "manager": 1, "ceo": 2}


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


async def resolve_role(
    session: SessionDep, *, character_id: int, corporation_id: int, is_ceo: bool
) -> tuple[Role, bool]:
    """Resolve (app role, corporation_registered) from the database (ADR-0016).

    Manager status is read fresh every call so grants/revokes take effect
    immediately. CEO status is supplied by the caller from the login-time
    identity, since it cannot be re-derived without an EVE token.
    """
    corp = await session.get(Corporation, corporation_id)
    registered = corp is not None
    if is_ceo:
        return "ceo", registered
    if registered:
        existing = await session.execute(
            select(ManagerAssignment).where(
                ManagerAssignment.corporation_id == corporation_id,
                ManagerAssignment.character_id == character_id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            return "manager", True
    return "member", registered


async def resolve_user(identity: SessionIdentity, session: SessionDep) -> SessionUser:
    """Build a freshly-resolved SessionUser from cookie identity + DB role."""
    role, registered = await resolve_role(
        session,
        character_id=identity.character_id,
        corporation_id=identity.corporation_id,
        is_ceo=identity.is_ceo,
    )
    return SessionUser(
        character_id=identity.character_id,
        character_name=identity.character_name,
        corporation_id=identity.corporation_id,
        corporation_name=identity.corporation_name,
        role=role,
        is_director=identity.is_director,
        corporation_registered=registered,
    )


async def get_current_user(
    identity: CurrentIdentity, session: SessionDep
) -> SessionUser | None:
    if identity is None:
        return None
    return await resolve_user(identity, session)


CurrentUser = Annotated[SessionUser | None, Depends(get_current_user)]


def require_user(user: CurrentUser) -> SessionUser:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    return user


RequireUser = Annotated[SessionUser, Depends(require_user)]


def require_role(minimum: Role) -> Callable[[SessionUser], SessionUser]:
    """Dependency factory: require at least `minimum` (member < manager < ceo).

    The role is resolved from the database per request, so a revoked privilege
    is enforced on the caller's very next request (ADR-0016).
    """

    def dependency(user: RequireUser) -> SessionUser:
        if ROLE_ORDER[user.role] < ROLE_ORDER[minimum]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires {minimum} role",
            )
        return user

    return dependency
