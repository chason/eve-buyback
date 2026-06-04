from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.schemas.auth import Role, SessionUser

SESSION_USER_KEY = "user"

ROLE_ORDER: dict[Role, int] = {"member": 0, "manager": 1, "ceo": 2}


def set_session_user(request: Request, user: SessionUser) -> None:
    request.session[SESSION_USER_KEY] = user.model_dump()


def clear_session(request: Request) -> None:
    request.session.clear()


def get_current_user(request: Request) -> SessionUser | None:
    data = request.session.get(SESSION_USER_KEY)
    return SessionUser(**data) if data else None


CurrentUser = Annotated[SessionUser | None, Depends(get_current_user)]


def require_user(user: CurrentUser) -> SessionUser:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    return user


RequireUser = Annotated[SessionUser, Depends(require_user)]


def require_role(minimum: Role) -> Callable[[SessionUser], SessionUser]:
    """Dependency factory: require at least `minimum` (member < manager < ceo)."""

    def dependency(user: RequireUser) -> SessionUser:
        if ROLE_ORDER[user.role] < ROLE_ORDER[minimum]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires {minimum} role",
            )
        return user

    return dependency
