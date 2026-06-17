"""structure token last_used_at for refresh rotation

Revision ID: 9d1c7e2a4b60
Revises: 0cb01ad2fb89
Create Date: 2026-06-17 12:30:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9d1c7e2a4b60'
down_revision: str | None = '0cb01ad2fb89'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'structure_market_tokens',
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('structure_market_tokens', 'last_used_at')
