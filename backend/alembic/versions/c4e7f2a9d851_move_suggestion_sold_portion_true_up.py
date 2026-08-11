"""move_suggestions gains qty_sold/qty_retired; expenses gain cost_true_up (#207)

The second sell-side signal (ADR-0049): the excess end of a "looks like a move"
pair can be already-sold stock — sale rows still carrying an estimated cost.
`qty_sold` records how much of the paired quantity that evidence carries and
`qty_retired` what the confirmation actually retired for it (0 until confirmed;
summed per destination so retired quantity never re-pairs). Both server-default
0 for the rows written so far.

`expense_kind` grows `cost_true_up` (ADR-0021 CHECK recreated, no data change):
the one aggregate correction a confirmed move books for the sold portion — real
landed cost minus the estimated COGS those sales recognized, positive or
negative. All existing kinds preserved. The column also WIDENS to VARCHAR(12):
`check_enum` sizes the column to the longest literal, and `cost_true_up`
(12 chars) outgrows the VARCHAR(10) that `broker_fee` fixed at creation —
without the widening, booking a true-up truncation-errors on any migrated
database (fresh `create_all` schemas already get 12).

Revision ID: c4e7f2a9d851
Revises: b3d5e8f1a742
Create Date: 2026-08-11 21:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c4e7f2a9d851'
down_revision: str | None = 'b3d5e8f1a742'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'move_suggestions',
        sa.Column(
            'qty_sold', sa.BigInteger(), nullable=False, server_default='0'
        ),
    )
    op.add_column(
        'move_suggestions',
        sa.Column(
            'qty_retired', sa.BigInteger(), nullable=False, server_default='0'
        ),
    )
    op.alter_column(
        "expenses",
        "kind",
        type_=sa.String(length=12),
        existing_type=sa.String(length=10),
        existing_nullable=False,
    )
    op.drop_constraint("expense_kind", "expenses", type_="check")
    op.create_check_constraint(
        "expense_kind",
        "expenses",
        "kind IN ('broker_fee', 'relist_fee', 'hauling', 'write_down', "
        "'other', 'cost_true_up')",
    )


def downgrade() -> None:
    # Fails if rows carry the new kind — delete them first; a downgrade should
    # not invent what to do with data that no longer fits.
    op.drop_constraint("expense_kind", "expenses", type_="check")
    op.create_check_constraint(
        "expense_kind",
        "expenses",
        "kind IN ('broker_fee', 'relist_fee', 'hauling', 'write_down', 'other')",
    )
    op.alter_column(
        "expenses",
        "kind",
        type_=sa.String(length=10),
        existing_type=sa.String(length=12),
        existing_nullable=False,
    )
    op.drop_column('move_suggestions', 'qty_retired')
    op.drop_column('move_suggestions', 'qty_sold')
