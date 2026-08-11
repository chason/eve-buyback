"""reconciliation_kind gains 'reprocess_recorded' (ADR-0050)

The hangar sync now records an observed reprocess automatically instead of only
suggesting it — the closed set (ADR-0021 CHECK on `reconciliation_events.kind`)
grows an eleventh value. No column widening needed: 'reprocess_recorded' (18)
fits inside the existing VARCHAR(19) ('unexpected_division').

Revision ID: e2a7c94f6b18
Revises: 4089b6409378
Create Date: 2026-08-11 23:30:00.000000

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e2a7c94f6b18'
down_revision: str | None = '4089b6409378'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "reconciliation_kind", "reconciliation_events", type_="check"
    )
    op.create_check_constraint(
        "reconciliation_kind",
        "reconciliation_events",
        "kind IN ('excess', 'shortfall', 'reprocess_hint', 'reprocess_recorded', "
        "'unmatched_sale', 'unexpected_division', 'move_confirmed', "
        "'move_dismissed', 'move_withdrawn', 'shipment_recorded', "
        "'shipment_arrived')",
    )


def downgrade() -> None:
    # Fails if reprocess_recorded rows exist — delete them first.
    op.drop_constraint(
        "reconciliation_kind", "reconciliation_events", type_="check"
    )
    op.create_check_constraint(
        "reconciliation_kind",
        "reconciliation_events",
        "kind IN ('excess', 'shortfall', 'reprocess_hint', 'unmatched_sale', "
        "'unexpected_division', 'move_confirmed', 'move_dismissed', "
        "'move_withdrawn', 'shipment_recorded', 'shipment_arrived')",
    )
