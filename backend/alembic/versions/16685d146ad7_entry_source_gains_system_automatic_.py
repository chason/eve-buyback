"""entry_source gains 'system' (automatic write-downs, #153)

The write-down sweep books expense rows itself — neither ESI-detected nor entered
by a manager — so the `entry_source` closed set (ADR-0021 CHECK constraints on
`expenses.source` and `sales.source`) grows a third value. Both tables are updated
to keep the shared domain Literal and the database in lockstep, though only
expenses are ever system-booked today.

Revision ID: 16685d146ad7
Revises: f07d87092084
Create Date: 2026-07-12 20:25:37.165283

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '16685d146ad7'
down_revision: str | None = 'f07d87092084'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("expenses", "sales")


def upgrade() -> None:
    for table in _TABLES:
        op.drop_constraint("entry_source", table, type_="check")
        op.create_check_constraint(
            "entry_source", table, "source IN ('esi', 'manual', 'system')"
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_constraint("entry_source", table, type_="check")
        op.create_check_constraint(
            "entry_source", table, "source IN ('esi', 'manual')"
        )
