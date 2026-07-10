"""Operator wallet authorization (ADR-0042).

An **app-admin-gated** SSO flow that persists one encrypted refresh token for the
instance operator's own character wallet — the credential payment reconciliation uses
to read the operator's wallet journal. Entirely separate from tenant Corp ESI tokens
(ADR-0029/0036): this token belongs to whoever runs the instance, never to a corp.
Access tokens are never persisted; they're refreshed server-side at point of use.
"""

import logging
from datetime import UTC, datetime

import httpx
from cryptography.fernet import InvalidToken
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.auth import LoginChallenge
from app.application.errors import (
    OperatorWalletExpired,
    OperatorWalletMissing,
    SsoNotConfigured,
    StructureEncryptionNotConfigured,
)
from app.config import get_settings
from app.data.records import OperatorWalletTokenRecord
from app.data.repositories import operator_wallet as wallet_repo
from app.domain import auth as auth_rules
from app.plugins.sso import EveSsoClient
from app.plugins.token_cipher import TokenCipher

log = logging.getLogger(__name__)

# All SSO flows share one callback (EVE allows a single redirect URI); the state is
# self-identifying so the SPA callback routes the round-trip (same trick as the corp
# ESI flow's "structure." prefix — login states are base64url and never contain ".").
WALLET_STATE_PREFIX = "opwallet."


def begin_wallet_authorize(sso: EveSsoClient) -> LoginChallenge:
    """Mint the PKCE/state challenge for the operator wallet grant. Fails fast on a
    missing encryption key so the admin isn't sent through the EVE round-trip only to
    have the completion refuse to store the token."""
    if not sso.configured:
        raise SsoNotConfigured()
    if not get_settings().corp_esi_token_configured:
        raise StructureEncryptionNotConfigured()
    state = WALLET_STATE_PREFIX + auth_rules.generate_state()
    verifier, challenge = auth_rules.generate_pkce()
    url = sso.build_authorize_url(
        state=state,
        code_challenge=challenge,
        scopes=get_settings().eve_wallet_scopes,
    )
    return LoginChallenge(authorization_url=url, state=state, verifier=verifier)


async def complete_wallet_authorize(
    session: AsyncSession,
    sso: EveSsoClient,
    *,
    code: str,
    verifier: str,
    cipher: TokenCipher,
) -> OperatorWalletTokenRecord:
    """Exchange the code and store the operator's encrypted refresh token, replacing
    any existing one (singleton). The old grant is signed out at EVE. The authorizing
    character can be any character the admin picks — it's the operator's own wallet,
    so there is no corp-membership constraint (caller is app-admin-gated)."""
    if not get_settings().corp_esi_token_configured:
        raise StructureEncryptionNotConfigured()

    token = await sso.exchange_code(code, verifier)
    if not token.refresh_token:
        raise OperatorWalletExpired("EVE did not return a refresh token")
    character = await sso.verify_token(token.access_token)

    existing = await wallet_repo.get(session)
    if existing is not None:
        old_refresh = _decrypt_or_none(cipher, existing.encrypted_refresh_token)
        if old_refresh is not None and old_refresh != token.refresh_token:
            await _revoke_at_eve(sso, old_refresh)

    record = await wallet_repo.replace(
        session,
        character_eve_id=character.character_id,
        character_name=character.name,
        encrypted_refresh_token=cipher.encrypt(token.refresh_token),
        scopes=get_settings().eve_wallet_scopes,
    )
    await session.commit()
    return record


async def get_status(session: AsyncSession) -> OperatorWalletTokenRecord | None:
    return await wallet_repo.get(session)


async def revoke(
    session: AsyncSession, sso: EveSsoClient, *, cipher: TokenCipher
) -> None:
    """Disconnect the operator wallet: sign the grant out at EVE, drop the row."""
    token = await wallet_repo.get(session)
    if token is None:
        raise OperatorWalletMissing()
    old_refresh = _decrypt_or_none(cipher, token.encrypted_refresh_token)
    if old_refresh is not None:
        await _revoke_at_eve(sso, old_refresh)
    await wallet_repo.delete_token(session)
    await session.commit()


async def get_wallet_access_token(
    session: AsyncSession, sso: EveSsoClient, *, cipher: TokenCipher
) -> tuple[int, str]:
    """(operator character id, fresh access token) for the reconciliation job,
    refreshing server-side and persisting a rotated refresh token. A revoked grant
    flags the row and raises `OperatorWalletExpired`."""
    if not get_settings().corp_esi_token_configured:
        raise StructureEncryptionNotConfigured()
    token = await wallet_repo.get(session)
    if token is None:
        raise OperatorWalletMissing()
    refresh_token = cipher.decrypt(token.encrypted_refresh_token)
    try:
        refreshed = await sso.refresh_access_token(refresh_token)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 400:  # invalid_grant: refresh revoked
            await wallet_repo.mark_failed(session, at=datetime.now(UTC))
            await session.commit()
            raise OperatorWalletExpired() from exc
        raise
    if refreshed.refresh_token and refreshed.refresh_token != refresh_token:
        await wallet_repo.update_refresh_token(
            session, encrypted_refresh_token=cipher.encrypt(refreshed.refresh_token)
        )
        await session.commit()
    return token.character_eve_id, refreshed.access_token


def _decrypt_or_none(cipher: TokenCipher, ciphertext: bytes) -> str | None:
    try:
        return cipher.decrypt(ciphertext)
    except InvalidToken:
        log.warning("Could not decrypt the stored operator wallet token to revoke it")
        return None


async def _revoke_at_eve(sso: EveSsoClient, refresh_token: str) -> None:
    """Best-effort revoke at EVE; a lingering grant isn't worth failing the action."""
    try:
        await sso.revoke_refresh_token(refresh_token)
    except httpx.HTTPError:
        log.warning("Revoking the operator wallet token at EVE failed", exc_info=True)
