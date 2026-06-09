"""Symmetric encryption for refresh tokens at rest (ADR-0029).

Structure-market access requires persisting an EVE **refresh token** — the one secret
the rest of the app deliberately avoids ([ADR-0004](../../docs/adr/0004-eve-sso-session-auth.md)).
It is stored Fernet-encrypted with `BUYBACK_TOKEN_ENCRYPTION_KEY`; only the ciphertext
ever touches the database, and access tokens are never persisted.
"""

from cryptography.fernet import Fernet

from app.config import get_settings


class TokenCipher:
    """Thin Fernet wrapper: `str` plaintext ⇄ `bytes` ciphertext."""

    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode())

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode())

    def decrypt(self, ciphertext: bytes) -> str:
        return self._fernet.decrypt(bytes(ciphertext)).decode()


def get_token_cipher() -> TokenCipher:
    """FastAPI dependency: a cipher built from the configured key."""
    return TokenCipher(get_settings().token_encryption_key)
