"""reconciliation_kind gains 'reprocess_hint' (ADR-0047, #177)

The hangar sync now recognizes a reprocessable-type shortfall alongside a
yield-consistent materials excess and logs it as a suggestion instead of a loss +
windfall — the closed set (ADR-0021 CHECK on `reconciliation_events.kind`) grows a
third value.

Revision ID: 81914dc0785e
Revises: 54621089bc75
Create Date: 2026-07-16 23:15:32.607267

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '81914dc0785e'
down_revision: str | None = '54621089bc75'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "reconciliation_kind", "reconciliation_events", type_="check"
    )
    # The ADR-0021 check_enum sizes the VARCHAR to the longest member at creation
    # ('shortfall', 9); the new value is 14 chars, so the column widens too.
    op.alter_column(
        "reconciliation_events",
        "kind",
        type_=sa.String(14),
        existing_nullable=False,
    )
    op.create_check_constraint(
        "reconciliation_kind",
        "reconciliation_events",
        "kind IN ('excess', 'shortfall', 'reprocess_hint')",
    )


def downgrade() -> None:
    # Fails if reprocess_hint rows exist — delete them first; narrowing a column
    # under data that no longer fits is not something a downgrade should invent.
    op.drop_constraint(
        "reconciliation_kind", "reconciliation_events", type_="check"
    )
    op.alter_column(
        "reconciliation_events",
        "kind",
        type_=sa.String(9),
        existing_nullable=False,
    )
    op.create_check_constraint(
        "reconciliation_kind",
        "reconciliation_events",
        "kind IN ('excess', 'shortfall')",
    )
