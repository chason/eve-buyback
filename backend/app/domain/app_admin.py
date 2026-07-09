"""Domain rule for the instance-level **app-admin** axis (ADR-0041).

An app admin is the operator of this hosted instance — an authorization axis **orthogonal**
to the per-corp member / manager / ceo model (an app admin is not a super-CEO of every
corp). This is the single place that decides admin-ness: today the allowlist comes from
config (`BUYBACK_ADMIN_CHARACTER_IDS`); a future DB-backed set can be unioned in by the
caller without touching any consumer.
"""

from collections.abc import Collection


def is_app_admin(character_id: int, admin_character_ids: Collection[int]) -> bool:
    """True if the character is an instance app admin (ADR-0041)."""
    return character_id in admin_character_ids
