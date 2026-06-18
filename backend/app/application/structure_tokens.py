"""Corp ESI access token (ADR-0029, ADR-0036).

A separate, **CEO/Director-gated** SSO flow that persists one **encrypted** EVE refresh
token per corp — covering both structure-market reads and corp-membership (the roster).
The normal login stays token-free (ADR-0004). Access tokens are never persisted —
they're refreshed server-side at point of use and held only transiently.

(The module/file keep the `structure_tokens` name to match the `structure_market_tokens`
table; the corp-ESI-token *functions* were renamed in ADR-0036.)
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime

import httpx
from cryptography.fernet import InvalidToken
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.auth import AuthenticatedUser, LoginChallenge
from app.application.corporations import get_registered_corporation
from app.application.errors import (
    AuthorizingCharacterNotInCorporation,
    CorpEsiTokenExpired,
    CorpEsiTokenMissing,
    NotAuthorizedToAuthorizeStructure,
    SsoNotConfigured,
    StructureEncryptionNotConfigured,
)
from app.config import get_settings
from app.data.records import StructureMarketTokenRecord
from app.data.repositories import characters as characters_repo
from app.data.repositories import structure_tokens as tokens_repo
from app.domain import auth as auth_rules
from app.plugins.esi import EsiClient
from app.plugins.esi_market import EsiMarketClient
from app.plugins.sso import EveSsoClient
from app.plugins.token_cipher import TokenCipher

log = logging.getLogger(__name__)

# The login and structure flows share one SSO callback (EVE allows a single
# redirect URI). The callback must route the round-trip to the right completion
# endpoint, so the structure flow's `state` is self-identifying with this prefix —
# the OAuth state is echoed back by EVE in the redirect, unlike fragile client-side
# storage. Login states are base64url (`token_urlsafe`) and never contain ".", so
# the prefix is unambiguous.
STRUCTURE_STATE_PREFIX = "structure."


class CorpEsiAuthorizeResult(BaseModel):
    """Outcome of completing a structure-access grant. `replaced_character_name` is
    set when a re-authorization switched the authorizing character (EVE can't pin the
    SSO picker), so the interface can warn that the token now belongs to someone else."""

    token: StructureMarketTokenRecord
    replaced_character_name: str | None = None


class StructureMatch(BaseModel):
    """A structure the authorizing character can access, matched by a name search
    (ADR-0029). The use case returns these (not loose dicts) so the interface maps a
    typed object to the API DTO. `structure_id` is stringified to match the API's
    string-id convention for EVE location ids."""

    model_config = ConfigDict(frozen=True)

    structure_id: str
    name: str


def begin_corp_esi_authorize(sso: EveSsoClient) -> LoginChallenge:
    """Mint the PKCE/state challenge for the **Corp ESI access** grant (ADR-0036): one
    token carrying both the structure-market scopes and the corp-membership scope. Fails
    fast on a missing/malformed encryption key so the admin isn't sent through the whole
    EVE round-trip only to have the completion refuse to store the token."""
    if not sso.configured:
        raise SsoNotConfigured()
    if not get_settings().corp_esi_token_configured:
        raise StructureEncryptionNotConfigured()
    state = STRUCTURE_STATE_PREFIX + auth_rules.generate_state()
    verifier, challenge = auth_rules.generate_pkce()
    url = sso.build_authorize_url(
        state=state,
        code_challenge=challenge,
        scopes=get_settings().eve_corp_token_scopes,
    )
    return LoginChallenge(authorization_url=url, state=state, verifier=verifier)


async def complete_corp_esi_authorize(
    session: AsyncSession,
    sso: EveSsoClient,
    esi: EsiClient,
    *,
    code: str,
    verifier: str,
    user: AuthenticatedUser,
    cipher: TokenCipher,
) -> CorpEsiAuthorizeResult:
    """Exchange the code and store the (encrypted) refresh token for the caller's corp
    (ADR-0036). Only the CEO or a Director may connect/revoke, but the authorizing
    character can be any corp member (validated here) — commonly a Director service
    character so the roster works too. A re-authorization replaces any existing token; if
    it switched to a different character, the previous character's name is returned so the
    caller can warn about the swap."""
    if not (user.role == "ceo" or user.is_director):
        raise NotAuthorizedToAuthorizeStructure()
    if not get_settings().corp_esi_token_configured:
        raise StructureEncryptionNotConfigured()

    corp = await get_registered_corporation(session, user.corporation_id)
    token = await sso.exchange_code(code, verifier)
    if not token.refresh_token:
        raise CorpEsiTokenExpired("EVE did not return a refresh token")
    character = await sso.verify_token(token.access_token)

    # The stored token must belong to a member of the connecting corp (it's the corp's
    # credential). The EVE picker lets the admin land on any of their characters.
    char_corp = await esi.get_character_corporation(character.character_id)
    if char_corp != user.corporation_id:
        raise AuthorizingCharacterNotInCorporation()

    # Note the outgoing character before we overwrite it — the picker is mandatory,
    # so a re-auth can silently land on a different character (warn, but allow).
    existing = await tokens_repo.get_for_corp(session, corp.id)
    replaced_character_name = (
        existing.character_name
        if existing is not None
        and existing.character_eve_id != character.character_id
        else None
    )

    # Sign the previous grant out at EVE — replacing the row only forgets the old
    # refresh token, leaving it live on EVE's side. A fresh code exchange always
    # mints a new refresh token, so the old one is safe to revoke.
    if existing is not None:
        old_refresh = _decrypt_or_none(cipher, existing.encrypted_refresh_token)
        if old_refresh is not None and old_refresh != token.refresh_token:
            await _revoke_at_eve(sso, old_refresh)

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
        # Record the full granted set (structure + membership), not just structures.
        scopes=get_settings().eve_corp_token_scopes,
    )
    await session.commit()
    return CorpEsiAuthorizeResult(
        token=record, replaced_character_name=replaced_character_name
    )


async def get_status(
    session: AsyncSession, *, corporation_id: int
) -> StructureMarketTokenRecord | None:
    """The corp's current authorization (or None), for the status view."""
    corp = await get_registered_corporation(session, corporation_id)
    return await tokens_repo.get_for_corp(session, corp.id)


async def revoke(
    session: AsyncSession,
    sso: EveSsoClient,
    *,
    corporation_id: int,
    cipher: TokenCipher,
) -> None:
    """Revoke the corp's structure authorization: sign the grant out at EVE (so the
    refresh token is actually killed, not just deleted locally), then drop the row."""
    corp = await get_registered_corporation(session, corporation_id)
    token = await tokens_repo.get_for_corp(session, corp.id)
    if token is None:
        raise CorpEsiTokenMissing()
    old_refresh = _decrypt_or_none(cipher, token.encrypted_refresh_token)
    if old_refresh is not None:
        await _revoke_at_eve(sso, old_refresh)
    await tokens_repo.delete_for_corp(session, corp.id)
    await session.commit()


def _decrypt_or_none(cipher: TokenCipher, ciphertext: bytes) -> str | None:
    """Decrypt a stored refresh token, or None if the key can no longer read it
    (e.g. the encryption key was rotated) — in which case we can't revoke it anyway."""
    try:
        return cipher.decrypt(ciphertext)
    except InvalidToken:
        log.warning("Could not decrypt a stored structure refresh token to revoke it")
        return None


async def _revoke_at_eve(sso: EveSsoClient, refresh_token: str) -> None:
    """Best-effort revoke at EVE. A lingering grant is not worth failing the user's
    action over, so transport/HTTP errors are logged and swallowed."""
    try:
        await sso.revoke_refresh_token(refresh_token)
    except httpx.HTTPError:
        log.warning("Revoking a structure refresh token at EVE failed", exc_info=True)


async def search_structures(
    session: AsyncSession,
    sso: EveSsoClient,
    esi_market: EsiMarketClient,
    *,
    corporation_id: int,
    query: str,
    cipher: TokenCipher,
) -> list[StructureMatch]:
    """Search the corp's accessible structures by name (ADR-0029), using the stored
    token's character. Returns typed `StructureMatch`es. Requires prior authorization."""
    corp = await get_registered_corporation(session, corporation_id)
    token = await tokens_repo.get_for_corp(session, corp.id)
    if token is None:
        raise CorpEsiTokenMissing()
    access_token = await get_corp_esi_access_token(
        session, sso, corporation_uuid=corp.id, cipher=cipher
    )
    structure_ids = await esi_market.search_structures(
        character_id=token.character_eve_id, query=query, access_token=access_token
    )
    # Resolve names concurrently — this is a typeahead path, so the per-id round
    # trips fan out rather than running serially. gather preserves order.
    names = await asyncio.gather(
        *(
            esi_market.resolve_structure_name(
                structure_id=structure_id, access_token=access_token
            )
            for structure_id in structure_ids
        )
    )
    return [
        StructureMatch(structure_id=str(structure_id), name=name)
        for structure_id, name in zip(structure_ids, names, strict=True)
        if name
    ]


async def get_corp_esi_access_token(
    session: AsyncSession,
    sso: EveSsoClient,
    *,
    corporation_uuid: uuid.UUID,
    cipher: TokenCipher,
) -> str:
    """Return a fresh access token for the corp's structure authorization, refreshing
    server-side. Persists a rotated refresh token (EVE may rotate it); on a revoked
    grant, flags the row and raises `CorpEsiTokenExpired`. Used by the structure
    pricing path (Phase B2)."""
    if not get_settings().corp_esi_token_configured:
        # The stored ciphertext can't be (safely) decrypted with a missing/malformed
        # key — refuse cleanly instead of raising from inside Fernet.
        raise StructureEncryptionNotConfigured()
    token = await tokens_repo.get_for_corp(session, corporation_uuid)
    if token is None:
        raise CorpEsiTokenMissing()
    refresh_token = cipher.decrypt(token.encrypted_refresh_token)
    try:
        refreshed = await sso.refresh_access_token(refresh_token)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 400:  # invalid_grant: refresh revoked
            await tokens_repo.mark_failed(
                session, corporation_id=corporation_uuid, at=datetime.now(UTC)
            )
            await session.commit()
            raise CorpEsiTokenExpired() from exc
        raise
    if refreshed.refresh_token and refreshed.refresh_token != refresh_token:
        await tokens_repo.update_refresh_token(
            session,
            corporation_id=corporation_uuid,
            encrypted_refresh_token=cipher.encrypt(refreshed.refresh_token),
        )
        await session.commit()
    return refreshed.access_token
