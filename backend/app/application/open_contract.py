"""Open a matched contract in the manager's EVE client (ADR-0038).

Uses the character's own **login** refresh token — kept Fernet-encrypted in the session
cookie, never server-side (amends ADR-0004) — to mint a fresh access token and call ESI's
open-window endpoint, so the contract opens in *their* running client. Returns the rotated
encrypted refresh token (EVE may issue a new one on use) for the interface to re-seal into
the cookie."""

import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.errors import NoMatchedContract, OpenContractUnavailable
from app.data.repositories import appraisal_contracts as links_repo
from app.data.repositories import corporations as corporations_repo
from app.plugins.esi import EsiClient, OpenWindowForbidden
from app.plugins.sso import EveSsoClient
from app.plugins.token_cipher import TokenCipher

log = logging.getLogger(__name__)


async def open_matched_contract(
    session: AsyncSession,
    sso: EveSsoClient,
    esi: EsiClient,
    cipher: TokenCipher,
    *,
    corporation_id: int,
    public_id: str,
    encrypted_login_token: str | None,
) -> str | None:
    """Open the appraisal's matched contract in EVE. Returns the (possibly rotated)
    encrypted login refresh token to persist back to the session, or None when unchanged.

    Raises `NoMatchedContract` (no linked contract for this corp's appraisal) or
    `OpenContractUnavailable` (no session token, a revoked grant, or a token without the
    open-window scope — all fixed by logging in again)."""
    corp = await corporations_repo.get_by_eve_id(session, corporation_id)
    if corp is None:
        raise NoMatchedContract()
    link = await links_repo.get_matched_contract(
        session, public_id=public_id, corporation_id=corp.id
    )
    if link is None:
        raise NoMatchedContract()

    if not encrypted_login_token:
        # Logged in before the open-window scope shipped (no token kept) → re-login.
        raise OpenContractUnavailable()

    try:
        token = await sso.refresh_access_token(
            cipher.decrypt(encrypted_login_token.encode())
        )
    except httpx.HTTPStatusError as exc:
        # The login grant was revoked/expired (invalid_grant) — the session token is dead.
        log.info("open-contract login refresh failed: %r", exc)
        raise OpenContractUnavailable() from exc

    try:
        await esi.open_contract_window(link.contract_id, token.access_token)
    except OpenWindowForbidden as exc:
        # The token predates the open-window scope → re-login to grant it.
        raise OpenContractUnavailable() from exc

    # EVE rotates the refresh token on use; hand the new one back to re-seal the cookie.
    return cipher.encrypt(token.refresh_token).decode() if token.refresh_token else None
