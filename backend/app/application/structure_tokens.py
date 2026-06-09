"""Structure-market access authorization (ADR-0029).

A separate, **manager-gated** SSO flow that persists an **encrypted** EVE refresh
token so the backend can read a player structure's market orders on the corp's
behalf. The normal login stays token-free (ADR-0004). Access tokens are never
persisted — they're refreshed server-side at point of use and held only transiently.
"""

import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.auth import AuthenticatedUser, LoginChallenge
from app.application.corporations import get_registered_corporation
from app.application.errors import (
    NotAuthorizedToAuthorizeStructure,
    SsoNotConfigured,
    StructureEncryptionNotConfigured,
    StructureTokenExpired,
    StructureTokenMissing,
)
from app.config import get_settings
from app.data.records import StructureMarketTokenRecord
from app.data.repositories import characters as characters_repo
from app.data.repositories import structure_tokens as tokens_repo
from app.domain import auth as auth_rules
from app.domain.roles import role_at_least
from app.plugins.sso import EveSsoClient
from app.plugins.token_cipher import TokenCipher


def begin_structure_authorize(sso: EveSsoClient) -> LoginChallenge:
    """Mint the PKCE/state challenge for the structure-scope authorization."""
    if not sso.configured:
        raise SsoNotConfigured()
    state = auth_rules.generate_state()
    verifier, challenge = auth_rules.generate_pkce()
    url = sso.build_authorize_url(
        state=state,
        code_challenge=challenge,
        scopes=get_settings().eve_structure_scopes,
    )
    return LoginChallenge(authorization_url=url, state=state, verifier=verifier)


async def complete_structure_authorize(
    session: AsyncSession,
    sso: EveSsoClient,
    *,
    code: str,
    verifier: str,
    user: AuthenticatedUser,
    cipher: TokenCipher,
) -> StructureMarketTokenRecord:
    """Exchange the code and store the (encrypted) refresh token for the caller's
    corp. Only a Buyback Manager / CEO may authorize."""
    if not role_at_least(user.role, "manager"):
        raise NotAuthorizedToAuthorizeStructure()
    if not get_settings().structure_tokens_configured:
        raise StructureEncryptionNotConfigured()

    corp = await get_registered_corporation(session, user.corporation_id)
    token = await sso.exchange_code(code, verifier)
    if not token.refresh_token:
        raise StructureTokenExpired("EVE did not return a refresh token")
    character = await sso.verify_token(token.access_token)
    char = await characters_repo.upsert_character(
        session, eve_character_id=character.character_id, name=character.name
    )
    record = await tokens_repo.upsert_token(
        session,
        corporation_id=corp.id,
        character_id=char.id,
        character_eve_id=character.character_id,
        character_name=character.name,
        encrypted_refresh_token=cipher.encrypt(token.refresh_token),
        scopes=get_settings().eve_structure_scopes,
    )
    await session.commit()
    return record


async def get_status(
    session: AsyncSession, *, corporation_id: int
) -> StructureMarketTokenRecord | None:
    """The corp's current authorization (or None), for the status view."""
    corp = await get_registered_corporation(session, corporation_id)
    return await tokens_repo.get_for_corp(session, corp.id)


async def revoke(session: AsyncSession, *, corporation_id: int) -> None:
    corp = await get_registered_corporation(session, corporation_id)
    removed = await tokens_repo.delete_for_corp(session, corp.id)
    if not removed:
        raise StructureTokenMissing()
    await session.commit()


async def get_structure_access_token(
    session: AsyncSession,
    sso: EveSsoClient,
    *,
    corporation_uuid: uuid.UUID,
    cipher: TokenCipher,
) -> str:
    """Return a fresh access token for the corp's structure authorization, refreshing
    server-side. Persists a rotated refresh token (EVE may rotate it); on a revoked
    grant, flags the row and raises `StructureTokenExpired`. Used by the structure
    pricing path (Phase B2)."""
    token = await tokens_repo.get_for_corp(session, corporation_uuid)
    if token is None:
        raise StructureTokenMissing()
    refresh_token = cipher.decrypt(token.encrypted_refresh_token)
    try:
        refreshed = await sso.refresh_access_token(refresh_token)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 400:  # invalid_grant: refresh revoked
            await tokens_repo.mark_failed(
                session, corporation_id=corporation_uuid, at=datetime.now(UTC)
            )
            await session.commit()
            raise StructureTokenExpired() from exc
        raise
    if refreshed.refresh_token and refreshed.refresh_token != refresh_token:
        await tokens_repo.update_refresh_token(
            session,
            corporation_id=corporation_uuid,
            encrypted_refresh_token=cipher.encrypt(refreshed.refresh_token),
        )
        await session.commit()
    return refreshed.access_token
