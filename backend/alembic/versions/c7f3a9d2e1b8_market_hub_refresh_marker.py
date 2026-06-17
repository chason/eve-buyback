"""market hub refresh marker (ADR-0034, #70)

Records the last time the background job fetched a hub's full order book — even an
empty one — so an illiquid structure (no rows written to market_prices) isn't
re-fetched every cycle.

Revision ID: c7f3a9d2e1b8
Revises: 9d1c7e2a4b60
Create Date: 2026-06-17 14:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7f3a9d2e1b8"
down_revision: str | None = "9d1c7e2a4b60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_hub_refreshes",
        sa.Column("hub_id", sa.String(), primary_key=True),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("market_hub_refreshes")
