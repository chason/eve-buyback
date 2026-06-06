from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.application import auth as auth_app
from app.interface.deps import SessionDep
from app.interface.security import RequireUser, clear_session, set_session_identity
from app.plugins.esi import EsiClient, get_esi_client
from app.plugins.sso import EveSsoClient, get_sso_client
from app.schemas.auth import LoginRequest, LoginUrlResponse, SessionUser

router = APIRouter(prefix="/auth")

SsoDep = Annotated[EveSsoClient, Depends(get_sso_client)]
EsiDep = Annotated[EsiClient, Depends(get_esi_client)]

OAUTH_STATE_KEY = "oauth_state"
PKCE_VERIFIER_KEY = "pkce_verifier"


@router.get("/login-url", response_model=LoginUrlResponse)
async def login_url(request: Request, sso: SsoDep) -> LoginUrlResponse:
    """Build the EVE SSO authorization URL and stash state + PKCE in the session."""
    challenge = auth_app.begin_login(sso)
    request.session[OAUTH_STATE_KEY] = challenge.state
    request.session[PKCE_VERIFIER_KEY] = challenge.verifier
    return LoginUrlResponse(
        authorization_url=challenge.authorization_url, state=challenge.state
    )


@router.post("/login", response_model=SessionUser)
async def login(
    payload: LoginRequest,
    request: Request,
    sso: SsoDep,
    esi: EsiDep,
    session: SessionDep,
) -> SessionUser:
    """Validate the OAuth state, then hand off to the login use case."""
    expected_state = request.session.get(OAUTH_STATE_KEY)
    verifier = request.session.get(PKCE_VERIFIER_KEY)
    if not expected_state or not verifier or payload.state != expected_state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OAuth state",
        )

    identity, user = await auth_app.complete_login(
        session, sso, esi, code=payload.code, verifier=verifier
    )

    request.session.pop(OAUTH_STATE_KEY, None)
    request.session.pop(PKCE_VERIFIER_KEY, None)
    set_session_identity(request, identity)
    return SessionUser(**user.model_dump())


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request) -> None:
    clear_session(request)


@router.get("/me", response_model=SessionUser)
async def me(user: RequireUser) -> SessionUser:
    return SessionUser(**user.model_dump())
