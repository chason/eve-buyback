"""Helper for closed-set columns (ADR-0021).

Builds a portable, CHECK-constrained string column type from a domain `Literal`,
keeping the Literal the single source of truth. `native_enum=False` +
`create_constraint=True` → `VARCHAR` + a `CHECK (col IN (...))` on both SQLite and
PostgreSQL, with no native-ENUM `ALTER TYPE` migration friction.
"""

from typing import get_args

from sqlalchemy import Enum


def check_enum(literal_type, *, name: str) -> Enum:
    return Enum(
        *get_args(literal_type),
        native_enum=False,
        create_constraint=True,
        name=name,
    )
