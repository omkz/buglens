"""add fix validation results

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-08-30 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "d2e3f4a5b6c7"
down_revision: Union[str, Sequence[str], None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("investigation_agent_runs", sa.Column("fix_validation_status", sa.String(), nullable=True))
    op.add_column("investigation_agent_runs", sa.Column("fix_validation_result", postgresql.JSONB(), nullable=True))
    op.add_column("investigation_agent_runs", sa.Column("fix_validation_started_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "ck_investigation_agent_runs_fix_validation_status",
        "investigation_agent_runs",
        "fix_validation_status IS NULL OR fix_validation_status IN "
        "('running', 'validated', 'validation_failed', 'stale_proposal', 'blocked', 'not_run')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_investigation_agent_runs_fix_validation_status", "investigation_agent_runs", type_="check")
    op.drop_column("investigation_agent_runs", "fix_validation_started_at")
    op.drop_column("investigation_agent_runs", "fix_validation_result")
    op.drop_column("investigation_agent_runs", "fix_validation_status")
