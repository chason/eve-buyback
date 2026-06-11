"""Symmetric encryption for refresh tokens at rest (ADR-0029).

Structure-market access requires persisting an EVE **refresh token** — the one secret
the rest of the app deliberately avoids ([ADR-0004](../../docs/adr/0004-eve-sso-session-auth.md)).
It is stored Fernet-encrypted with `BUYBACK_TOKEN_ENCRYPTION_KEY`; only the ciphertext
ever touches the database, and access tokens are never persisted.
"""

from cryptography.fernet import Fernet

from app.config import get_settings


class TokenCipher:
    """Thin Fernet wrapper: `str` plaintext ⇄ `bytes` ciphertext.

    The Fernet is built **lazily** on first use: this object is constructed as a
    FastAPI dependency on every request that *might* need it (including ordinary
    appraisals), so a malformed `BUYBACK_TOKEN_ENCRYPTION_KEY` must not take those
    endpoints down — the structure-token use cases gate on
    `settings.structure_tokens_configured` and fail with a clean typed error before
    any encrypt/decrypt happens.
    """

    def __init__(self, key: str) -> None:
        self._key = key
        self._fernet: Fernet | None = None

    def _get_fernet(self) -> Fernet:
        if self._fernet is None:
            self._fernet = Fernet(self._key.encode())
        return self._fernet

    def encrypt(self, plaintext: str) -> bytes:
        return self._get_fernet().encrypt(plaintext.encode())

    def decrypt(self, ciphertext: bytes) -> str:
        return self._get_fernet().decrypt(bytes(ciphertext)).decode()


def get_token_cipher() -> TokenCipher:
    """FastAPI dependency: a cipher built from the configured key."""
    return TokenCipher(get_settings().token_encryption_key)
