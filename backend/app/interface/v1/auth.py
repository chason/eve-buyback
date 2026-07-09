from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.application import auth as auth_app
from app.interface.deps import SessionDep
from app.interface.security import (
    IsAppAdmin,
    RequireUser,
    clear_session,
    is_user_app_admin,
    set_session_identity,
)
from app.plugins.esi import EsiClient, get_esi_client
from app.plugins.sso import EveSsoClient, get_sso_client
from app.plugins.token_cipher import TokenCipher, get_token_cipher
from app.schemas.auth import LoginRequest, LoginUrlResponse, SessionUser

router = APIRouter(prefix="/auth")

SsoDep = Annotated[EveSsoClient, Depends(get_sso_client)]
EsiDep = Annotated[EsiClient, Depends(get_esi_client)]
CipherDep = Annotated[TokenCipher, Depends(get_token_cipher)]

OAUTH_STATE_KEY = "oauth_state"
PKCE_VERIFIER_KEY = "pkce_verifier"


@router.post("/login", response_model=LoginUrlResponse)
async def begin_login(request: Request, sso: SsoDep) -> LoginUrlResponse:
    """Begin login: mint state + PKCE (stashed in the session) and return the EVE
    authorization URL for the SPA to redirect to."""
    challenge = auth_app.begin_login(sso)
    request.session[OAUTH_STATE_KEY] = challenge.state
    request.session[PKCE_VERIFIER_KEY] = challenge.verifier
    return LoginUrlResponse(
        authorization_url=challenge.authorization_url, state=challenge.state
    )


@router.post("/session", response_model=SessionUser)
async def create_session(
    payload: LoginRequest,
    request: Request,
    sso: SsoDep,
    esi: EsiDep,
    session: SessionDep,
    cipher: CipherDep,
) -> SessionUser:
    """Complete login: validate the OAuth state, exchange the code, open a session."""
    expected_state = request.session.get(OAUTH_STATE_KEY)
    verifier = request.session.get(PKCE_VERIFIER_KEY)
    if not expected_state or not verifier or payload.state != expected_state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OAuth state",
        )

    identity, user = await auth_app.complete_login(
        session, sso, esi, code=payload.code, verifier=verifier, cipher=cipher
    )

    request.session.pop(OAUTH_STATE_KEY, None)
    request.session.pop(PKCE_VERIFIER_KEY, None)
    set_session_identity(request, identity)
    return SessionUser(**user.model_dump(), is_app_admin=is_user_app_admin(user))


@router.delete("/session", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(request: Request) -> None:
    """Log out: clear the session cookie."""
    clear_session(request)


@router.get("/me", response_model=SessionUser)
async def me(user: RequireUser, is_admin: IsAppAdmin) -> SessionUser:
    return SessionUser(**user.model_dump(), is_app_admin=is_admin)
