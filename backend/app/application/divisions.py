"""Corp division names (ADR-0048): the real wallet/hangar division names for the
Stock page's pickers, read via the Corp ESI access token.

Best-effort by design — the names are labels, nothing else keys off them. No token,
an expired grant, a missing scope (a grant predating ADR-0048), or a non-Director
authorizing character all degrade to empty maps; the UI falls back to the generic
"Wallet division N" / "Hangar N" labels.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.application import corp_esi_token as corp_esi_token_app
from app.application.corporations import get_registered_corporation
from app.application.errors import (
    CorpEsiTokenExpired,
    CorpEsiTokenMissing,
    StructureEncryptionNotConfigured,
)
from app.plugins.esi import CorporationDivisions, EsiClient
from app.plugins.sso import EveSsoClient
from app.plugins.token_cipher import TokenCipher


async def get_division_names(
    session: AsyncSession,
    sso: EveSsoClient,
    esi: EsiClient,
    *,
    corporation_eve_id: int,
    cipher: TokenCipher,
) -> CorporationDivisions:
    """The corp's named divisions, or empty maps when they can't be read."""
    corp = await get_registered_corporation(session, corporation_eve_id)
    try:
        access_token = await corp_esi_token_app.get_corp_esi_access_token(
            session, sso, corporation_uuid=corp.id, cipher=cipher
        )
    except (
        CorpEsiTokenMissing,
        CorpEsiTokenExpired,
        StructureEncryptionNotConfigured,
    ):
        return CorporationDivisions()
    # A missing scope / in-game role degrades to empty inside the plugin (401/403).
    return await esi.get_corporation_divisions(corporation_eve_id, access_token)
