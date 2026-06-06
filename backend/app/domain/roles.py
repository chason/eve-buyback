"""Domain rules for the member / Buyback Manager / CEO authorization model
(ADR-0005). Small, single-purpose functions with no I/O — called by use cases."""

from typing import Literal

Role = Literal["member", "manager", "ceo"]

ROLE_ORDER: dict[Role, int] = {"member": 0, "manager": 1, "ceo": 2}


def derive_role(*, is_ceo: bool, is_manager: bool) -> Role:
    """Resolve the effective role from the underlying facts (ADR-0016)."""
    if is_ceo:
        return "ceo"
    if is_manager:
        return "manager"
    return "member"


def role_at_least(role: Role, minimum: Role) -> bool:
    """True if `role` meets or exceeds `minimum` (member < manager < ceo)."""
    return ROLE_ORDER[role] >= ROLE_ORDER[minimum]
