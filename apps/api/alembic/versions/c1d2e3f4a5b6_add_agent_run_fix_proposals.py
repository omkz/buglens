"""add agent run fix proposals

Revision ID: c1d2e3f4a5b6
Revises: b0f1c2d3e4f5
Create Date: 2026-08-30 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, Sequence[str], None] = "b0f1c2d3e4f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Persist the validated proposal and optional no-fix reason."""
    op.add_column(
        "investigation_agent_runs",
        sa.Column("fix_proposal", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "investigation_agent_runs",
        sa.Column("fix_proposal_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Remove persisted fix proposal data."""
    op.drop_column("investigation_agent_runs", "fix_proposal_reason")
    op.drop_column("investigation_agent_runs", "fix_proposal")
