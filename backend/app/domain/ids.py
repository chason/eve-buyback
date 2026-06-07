"""Identifier helpers. Pure (uses `secrets`, like `domain/auth.py`)."""

import secrets


def generate_appraisal_id() -> str:
    """A random, URL-safe, non-sequential share handle for an appraisal (ADR-0014).
    9 bytes → a fixed 12-char base64url slug carrying 72 bits of entropy: ample to
    avoid collisions and resist enumeration."""
    return secrets.token_urlsafe(9)
