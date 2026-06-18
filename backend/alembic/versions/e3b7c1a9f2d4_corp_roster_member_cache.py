"""corp roster member cache (ADR-0036, manager designation)

Caches a corporation's member list (EVE character id + name) as of the last
manager-roster sync, so the "designate a Buyback Manager" picker can search real corp
members. Replaced wholesale on each sync; no EVE token is persisted.

Revision ID: e3b7c1a9f2d4
Revises: c7f3a9d2e1b8
Create Date: 2026-06-18 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e3b7c1a9f2d4"
down_revision: str | None = "c7f3a9d2e1b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "corp_roster_members",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "corporation_id",
            sa.Uuid(),
            sa.ForeignKey("corporations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # BigInteger: the roster ingests every corp member, including high (>2^31) ids.
        sa.Column("character_eve_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "corporation_id", "character_eve_id", name="uq_roster_corp_char"
        ),
    )
    op.create_index(
        "ix_corp_roster_members_corporation_id",
        "corp_roster_members",
        ["corporation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_corp_roster_members_corporation_id", table_name="corp_roster_members"
    )
    op.drop_table("corp_roster_members")
