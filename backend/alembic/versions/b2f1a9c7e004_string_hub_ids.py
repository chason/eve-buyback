"""string hub ids for structures (ADR-0029)

Hub/location ids (market_prices.hub_id, buyback_configs.market_hub_id,
appraisals.market_hub_id) become strings so they hold 64-bit player-structure ids
and are free of int32/JS-number range concerns.

Revision ID: b2f1a9c7e004
Revises: 7cb3ff91777c
Create Date: 2026-06-09

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2f1a9c7e004"
down_revision: str | None = "7cb3ff91777c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "market_prices", "hub_id",
        type_=sa.String(), postgresql_using="hub_id::text",
    )
    op.alter_column(
        "buyback_configs", "market_hub_id",
        type_=sa.String(), postgresql_using="market_hub_id::text",
    )
    op.alter_column(
        "appraisals", "market_hub_id",
        type_=sa.String(), postgresql_using="market_hub_id::text",
    )


def downgrade() -> None:
    # Only safe before any structure hub is used: a 64-bit structure id cast back to
    # int32 (`::integer`) overflows and raises. Drop such rows first if downgrading.
    op.alter_column(
        "appraisals", "market_hub_id",
        type_=sa.Integer(), postgresql_using="market_hub_id::integer",
    )
    op.alter_column(
        "buyback_configs", "market_hub_id",
        type_=sa.Integer(), postgresql_using="market_hub_id::integer",
    )
    op.alter_column(
        "market_prices", "hub_id",
        type_=sa.Integer(), postgresql_using="hub_id::integer",
    )
