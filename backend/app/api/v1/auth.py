from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select

from app.auth.session import RequireUser, clear_session, set_session_user
from app.auth.sso import EveSsoClient, generate_pkce, generate_state, get_sso_client
from app.deps import SessionDep
from app.eve.esi import EsiClient, get_esi_client
from app.models import Character, Corporation, ManagerAssignment
from app.schemas.auth import LoginRequest, LoginUrlResponse, Role, SessionUser

router = APIRouter(prefix="/auth")

SsoDep = Annotated[EveSsoClient, Depends(get_sso_client)]
EsiDep = Annotated[EsiClient, Depends(get_esi_client)]

OAUTH_STATE_KEY = "oauth_state"
PKCE_VERIFIER_KEY = "pkce_verifier"


def _ensure_configured(sso: EveSsoClient) -> None:
    if not sso.configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="EVE SSO is not configured",
        )


@router.get("/login-url", response_model=LoginUrlResponse)
async def login_url(request: Request, sso: SsoDep) -> LoginUrlResponse:
    """Build the EVE SSO authorization URL and stash state + PKCE in the session."""
    _ensure_configured(sso)
    state = generate_state()
    verifier, challenge = generate_pkce()
    request.session[OAUTH_STATE_KEY] = state
    request.session[PKCE_VERIFIER_KEY] = verifier
    return LoginUrlResponse(
        authorization_url=sso.build_authorize_url(state=state, code_challenge=challenge),
        state=state,
    )


async def _resolve_role(
    session: SessionDep, *, character_id: int, corporation_id: int, is_ceo: bool
) -> tuple[Role, bool]:
    """Return (app role, corporation_registered) from the database."""
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


@router.post("/login", response_model=SessionUser)
async def login(
    payload: LoginRequest,
    request: Request,
    sso: SsoDep,
    esi: EsiDep,
    session: SessionDep,
) -> SessionUser:
    """Exchange the SSO code for identity, resolve corp/role, and open a session."""
    _ensure_configured(sso)

    expected_state = request.session.get(OAUTH_STATE_KEY)
    verifier = request.session.get(PKCE_VERIFIER_KEY)
    if not expected_state or not verifier or payload.state != expected_state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OAuth state",
        )

    token = await sso.exchange_code(payload.code, verifier)
    character = await sso.verify_token(token.access_token)

    corporation_id = await esi.get_character_corporation(character.character_id)
    corporation = await esi.get_corporation(corporation_id)
    roles = await esi.get_character_roles(character.character_id, token.access_token)
    is_director = "Director" in roles

    # Upsert the character record (managers must be referenceable).
    char = await session.get(Character, character.character_id)
    if char is None:
        session.add(Character(character_id=character.character_id, name=character.name))
    else:
        char.name = character.name
        char.last_login_at = datetime.now(UTC)

    is_ceo = character.character_id == corporation.ceo_id
    role, corporation_registered = await _resolve_role(
        session,
        character_id=character.character_id,
        corporation_id=corporation_id,
        is_ceo=is_ceo,
    )
    await session.commit()

    user = SessionUser(
        character_id=character.character_id,
        character_name=character.name,
        corporation_id=corporation_id,
        corporation_name=corporation.name,
        role=role,
        is_director=is_director,
        corporation_registered=corporation_registered,
    )

    request.session.pop(OAUTH_STATE_KEY, None)
    request.session.pop(PKCE_VERIFIER_KEY, None)
    set_session_user(request, user)
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request) -> None:
    clear_session(request)


@router.get("/me", response_model=SessionUser)
async def me(user: RequireUser) -> SessionUser:
    return user
