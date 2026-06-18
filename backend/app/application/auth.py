"""Authentication use cases: begin the SSO login, complete it, and resolve the
authenticated principal. Orchestrates the SSO/ESI plugins, the data layer, and
domain rules — but knows nothing about HTTP or sessions."""

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.errors import SsoNotConfigured
from app.data.repositories import characters as characters_repo
from app.data.repositories import corporations as corporations_repo
from app.data.repositories import managers as managers_repo
from app.domain import auth as auth_rules
from app.domain.roles import Role, derive_role
from app.plugins.esi import EsiClient
from app.plugins.sso import EveSsoClient
from app.plugins.token_cipher import TokenCipher


class SessionIdentity(BaseModel):
    """Stable identity established at login (ADR-0016). The interface persists
    this in the signed session cookie; the role is resolved separately per
    request. `is_ceo`/`is_director` come from ESI and cannot be re-derived
    without an EVE token, so they are carried here.

    `encrypted_login_token` is the character's **login** refresh token, Fernet-encrypted
    (ADR-0038, amends ADR-0004): kept only in this http-only cookie — never server-side —
    so a manager can open a matched contract in their own EVE client. Absent for sessions
    opened before that feature; rotated in place when EVE issues a new refresh token."""

    character_id: int
    character_name: str
    corporation_id: int
    corporation_name: str
    is_director: bool = False
    is_ceo: bool = False
    encrypted_login_token: str | None = None


class AuthenticatedUser(BaseModel):
    """The resolved principal: identity plus the DB-resolved role/registration."""

    character_id: int
    character_name: str
    corporation_id: int
    corporation_name: str
    role: Role
    is_director: bool = False
    corporation_registered: bool = False
    # Whether this session can open a contract in EVE (ADR-0038): true once the session
    # holds a login token (i.e. logged in after the open-window scope was added).
    can_open_contract: bool = False


class LoginChallenge(BaseModel):
    authorization_url: str
    state: str
    verifier: str


def begin_login(sso: EveSsoClient) -> LoginChallenge:
    """Generate the PKCE/state challenge and the EVE authorization URL."""
    if not sso.configured:
        raise SsoNotConfigured()
    state = auth_rules.generate_state()
    verifier, challenge = auth_rules.generate_pkce()
    url = sso.build_authorize_url(state=state, code_challenge=challenge)
    return LoginChallenge(authorization_url=url, state=state, verifier=verifier)


async def complete_login(
    session: AsyncSession,
    sso: EveSsoClient,
    esi: EsiClient,
    *,
    code: str,
    verifier: str,
    cipher: TokenCipher,
) -> tuple[SessionIdentity, AuthenticatedUser]:
    """Exchange the code, look up the character/corp, persist the character, and
    return the cookie identity plus the resolved user. The login refresh token is kept
    **encrypted in the session cookie** (ADR-0038) to power "Open in EVE" — never stored
    server-side; the access token is still discarded after reading roles."""
    if not sso.configured:
        raise SsoNotConfigured()

    token = await sso.exchange_code(code, verifier)
    character = await sso.verify_token(token.access_token)

    corporation_id = await esi.get_character_corporation(character.character_id)
    corporation = await esi.get_corporation(corporation_id)
    corp_roles = await esi.get_character_roles(
        character.character_id, token.access_token
    )

    await characters_repo.upsert_character(
        session, eve_character_id=character.character_id, name=character.name
    )
    await session.commit()

    identity = SessionIdentity(
        character_id=character.character_id,
        character_name=character.name,
        corporation_id=corporation_id,
        corporation_name=corporation.name,
        is_director=auth_rules.is_director(corp_roles),
        is_ceo=auth_rules.is_ceo(character.character_id, corporation.ceo_id),
        # Fernet ciphertext is urlsafe-base64 ASCII; keep it as `str` so it serializes
        # into the JSON session cookie (the cipher works in `bytes`).
        encrypted_login_token=(
            cipher.encrypt(token.refresh_token).decode()
            if token.refresh_token
            else None
        ),
    )
    user = await resolve_authenticated_user(session, identity)
    return identity, user


async def resolve_authenticated_user(
    session: AsyncSession, identity: SessionIdentity
) -> AuthenticatedUser:
    """Resolve role + registration freshly from the database (ADR-0016). Identity is
    in EVE ids; manager-ness is checked against the internal UUIDs (ADR-0025)."""
    corp = await corporations_repo.get_by_eve_id(session, identity.corporation_id)
    char = await characters_repo.get_by_eve_id(session, identity.character_id)
    registered = corp is not None
    is_manager = (
        registered
        and char is not None
        and await managers_repo.manager_exists(
            session, corporation_id=corp.id, character_id=char.id
        )
    )
    role = derive_role(is_ceo=identity.is_ceo, is_manager=is_manager)
    return AuthenticatedUser(
        character_id=identity.character_id,
        character_name=identity.character_name,
        corporation_id=identity.corporation_id,
        corporation_name=identity.corporation_name,
        role=role,
        is_director=identity.is_director,
        corporation_registered=registered,
        can_open_contract=identity.encrypted_login_token is not None,
    )
