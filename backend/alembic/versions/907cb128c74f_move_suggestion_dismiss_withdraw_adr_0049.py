"""move-suggestion dismiss & withdraw (ADR-0049, #202)

Two closed sets grow (ADR-0021 CHECKs recreated, no data change):

- `move_suggestions.status` gains 'withdrawn'. A dismissal is a human's "not a
  move" and suppresses re-suggesting the unchanged pattern; a withdrawal is
  bookkeeping — a later sync invalidated the pair — and must NOT suppress a
  future pairing, so the two need distinct statuses. Same width (9 chars) as
  'confirmed'/'dismissed' — no column change.
- `reconciliation_events.kind` gains 'move_dismissed' and 'move_withdrawn', the
  log entries those two exits leave. Both fit the existing VARCHAR(19).

Revision ID: 907cb128c74f
Revises: a91c4f2b7d13
Create Date: 2026-08-11

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '907cb128c74f'
down_revision: str | None = 'a91c4f2b7d13'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "move_suggestion_status", "move_suggestions", type_="check"
    )
    op.create_check_constraint(
        "move_suggestion_status",
        "move_suggestions",
        "status IN ('pending', 'confirmed', 'dismissed', 'withdrawn')",
    )
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


def downgrade() -> None:
    # Fails if rows carry the new values — delete them first; a downgrade should
    # not invent what to do with data that no longer fits.
    op.drop_constraint(
        "move_suggestion_status", "move_suggestions", type_="check"
    )
    op.create_check_constraint(
        "move_suggestion_status",
        "move_suggestions",
        "status IN ('pending', 'confirmed', 'dismissed')",
    )
    op.drop_constraint(
        "reconciliation_kind", "reconciliation_events", type_="check"
    )
    op.create_check_constraint(
        "reconciliation_kind",
        "reconciliation_events",
        "kind IN ('excess', 'shortfall', 'reprocess_hint', 'unmatched_sale', "
        "'unexpected_division', 'move_confirmed')",
    )
