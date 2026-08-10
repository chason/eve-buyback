"""sale cost snapshot (159 profit view)

Revision ID: bff4e5eeda6c
Revises: 7e4bdad3ab3e
Create Date: 2026-08-10 23:23:49.487599

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bff4e5eeda6c'
down_revision: str | None = '7e4bdad3ab3e'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Sale-time COGS snapshot (#159). Backfill from the consumed lot's CURRENT
    # landed cost — the best remaining evidence for pre-feature rows (a lot written
    # down between the sale and this migration backfills slightly low; new rows
    # snapshot exactly at sale time).
    op.add_column('sales', sa.Column('unit_cost', sa.Numeric(), nullable=True))
    op.add_column(
        'sales',
        sa.Column(
            'cost_is_estimated',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
    )
    op.execute(
        """
        UPDATE sales SET
            unit_cost = LEAST(
                lots.unit_purchase_cost + lots.unit_hauling_cost,
                COALESCE(
                    lots.written_down_to,
                    lots.unit_purchase_cost + lots.unit_hauling_cost
                )
            ),
            cost_is_estimated = lots.cost_is_estimated
        FROM lots WHERE lots.id = sales.lot_id
        """
    )
    op.alter_column('sales', 'unit_cost', nullable=False)
    op.alter_column('sales', 'cost_is_estimated', server_default=None)


def downgrade() -> None:
    op.drop_column('sales', 'cost_is_estimated')
    op.drop_column('sales', 'unit_cost')
