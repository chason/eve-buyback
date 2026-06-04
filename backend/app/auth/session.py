from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.schemas.auth import SessionUser

SESSION_USER_KEY = "user"


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
