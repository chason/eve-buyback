"""rename structure_market_tokens → corp_esi_tokens (ADR-0036)

The persisted structure-market token is now the corp's single ESI credential — it also
carries the corp-membership scope used for the roster — so the table is renamed to match
the `CorpEsiToken` model. Data-preserving rename; the (auto-named) unique/FK constraints
keep their original names, which is cosmetic and doesn't affect behavior.

Revision ID: f1a2c3d4e5b6
Revises: e3b7c1a9f2d4
Create Date: 2026-06-18 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "f1a2c3d4e5b6"
down_revision: str | None = "e3b7c1a9f2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.rename_table("structure_market_tokens", "corp_esi_tokens")


def downgrade() -> None:
    op.rename_table("corp_esi_tokens", "structure_market_tokens")
