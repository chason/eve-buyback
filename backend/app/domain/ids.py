"""Identifier helpers. Pure (uses `secrets`, like `domain/auth.py`)."""

import secrets


def _slug() -> str:
    """A random, URL-safe, non-sequential handle. 9 bytes → a fixed 12-char
    base64url slug carrying 72 bits of entropy: ample to avoid collisions and
    resist enumeration."""
    return secrets.token_urlsafe(9)


def generate_appraisal_id() -> str:
    """Public share handle for a persisted appraisal (ADR-0014)."""
    return _slug()


def generate_rule_id() -> str:
    """Public, non-enumerable handle for a pricing rule (ADR-0022) — keeps the
    sequential surrogate PK out of the API."""
    return _slug()
