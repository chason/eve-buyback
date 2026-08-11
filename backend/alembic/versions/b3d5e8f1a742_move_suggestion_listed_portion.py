"""move_suggestions gains qty_listed (ADR-0049 sell-side pairing, #206)

The excess end of a "looks like a move" pair can now be the division's
unexplained sell-order escrow at any station it trades at. `qty_listed` records
how much of the paired quantity that evidence carries (0 for the pure
hangar-counted pairs written so far — the server default backfills them);
`excess_lot_id` was already nullable, and stays NULL for pure sell-side pairs.

Revision ID: b3d5e8f1a742
Revises: d4e9f1a3b527
Create Date: 2026-08-11 18:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b3d5e8f1a742'
down_revision: str | None = 'd4e9f1a3b527'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'move_suggestions',
        sa.Column(
            'qty_listed', sa.BigInteger(), nullable=False, server_default='0'
        ),
    )


def downgrade() -> None:
    op.drop_column('move_suggestions', 'qty_listed')
