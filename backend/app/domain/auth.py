"""Domain helpers for the EVE SSO login flow. Pure functions — no I/O."""

import base64
import hashlib
import secrets

DIRECTOR_ROLE = "Director"


def generate_state() -> str:
    """Opaque anti-CSRF state for the OAuth round-trip (ADR-0004)."""
    return secrets.token_urlsafe(24)


def generate_pkce() -> tuple[str, str]:
    """Return (verifier, S256 challenge) for the PKCE flow."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def is_director(corp_roles: list[str]) -> bool:
    """Whether the character holds the EVE in-game Director role (ADR-0015)."""
    return DIRECTOR_ROLE in corp_roles


def is_ceo(character_id: int, corporation_ceo_id: int) -> bool:
    return character_id == corporation_ceo_id
