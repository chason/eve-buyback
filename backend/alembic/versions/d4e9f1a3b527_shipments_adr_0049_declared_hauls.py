"""shipments (ADR-0049, #208 declared hauls)

A manager records a move proactively: the `shipments` table holds the haul
(open → arrived), and while open it is the in-transit allocation (ADR-0043) the
reconciliation excludes from idle at both ends. Both lifecycle steps log, so
the reconciliation-kind closed set (ADR-0021 CHECK) grows 'shipment_recorded'
and 'shipment_arrived'. No column widening needed: both fit inside the existing
VARCHAR(19) ('unexpected_division').

Revision ID: d4e9f1a3b527
Revises: 907cb128c74f
Create Date: 2026-08-11 18:40:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd4e9f1a3b527'
down_revision: str | None = '907cb128c74f'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('shipments',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('corporation_id', sa.Uuid(), nullable=False),
    sa.Column('type_id', sa.Integer(), nullable=False),
    sa.Column('origin_location_id', sa.String(), nullable=False),
    sa.Column('destination_location_id', sa.String(), nullable=False),
    sa.Column('qty', sa.BigInteger(), nullable=False),
    sa.Column('status', sa.Enum('open', 'arrived', name='shipment_status', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('arrived_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['corporation_id'], ['corporations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_shipments_corporation_id'), 'shipments', ['corporation_id'], unique=False)
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


def downgrade() -> None:
    # Fails if shipment_recorded/shipment_arrived rows exist — delete them first;
    # a downgrade should not invent what to do with data that no longer fits.
    op.drop_constraint(
        "reconciliation_kind", "reconciliation_events", type_="check"
    )
    op.create_check_constraint(
        "reconciliation_kind",
        "reconciliation_events",
        "kind IN ('excess', 'shortfall', 'reprocess_hint', 'unmatched_sale', "
        "'unexpected_division', 'move_confirmed', 'move_dismissed', "
        "'move_withdrawn')",
    )
    op.drop_index(op.f('ix_shipments_corporation_id'), table_name='shipments')
    op.drop_table('shipments')
