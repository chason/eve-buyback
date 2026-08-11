"""reconciliation_kind gains 'move_confirmed' (ADR-0049, #201)

Confirming a "looks like a move" suggestion logs the conversion in the
reconciliation log — the closed set (ADR-0021 CHECK on
`reconciliation_events.kind`) grows a sixth value. No column widening needed:
'move_confirmed' (14) fits inside the existing VARCHAR(19)
('unexpected_division').

Revision ID: a91c4f2b7d13
Revises: f7eedcc96e85
Create Date: 2026-08-11 16:20:00.000000

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a91c4f2b7d13'
down_revision: str | None = 'f7eedcc96e85'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "reconciliation_kind", "reconciliation_events", type_="check"
    )
    op.create_check_constraint(
        "reconciliation_kind",
        "reconciliation_events",
        "kind IN ('excess', 'shortfall', 'reprocess_hint', 'unmatched_sale', "
        "'unexpected_division', 'move_confirmed')",
    )


def downgrade() -> None:
    # Fails if move_confirmed rows exist — delete them first.
    op.drop_constraint(
        "reconciliation_kind", "reconciliation_events", type_="check"
    )
    op.create_check_constraint(
        "reconciliation_kind",
        "reconciliation_events",
        "kind IN ('excess', 'shortfall', 'reprocess_hint', 'unmatched_sale', "
        "'unexpected_division')",
    )
