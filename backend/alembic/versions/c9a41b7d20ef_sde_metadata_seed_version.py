"""sde_metadata seed_version (auto re-seed on coverage changes)

Revision ID: c9a41b7d20ef
Revises: bff4e5eeda6c
Create Date: 2026-08-11 02:05:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c9a41b7d20ef'
down_revision: str | None = 'bff4e5eeda6c'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # What the seed covered (application SEED_VERSION at import time). Existing
    # stamps stay NULL — read as "older than any version", so the boot-time
    # auto-seed re-runs once and stamps the current version.
    op.add_column(
        'sde_metadata', sa.Column('seed_version', sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('sde_metadata', 'seed_version')
