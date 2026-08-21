"""add agent run progress

Revision ID: a9e0f1b2c3d4
Revises: f8d9e0a1b2c3
Create Date: 2026-08-21 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a9e0f1b2c3d4"
down_revision: Union[str, Sequence[str], None] = "f8d9e0a1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the current persisted progress snapshot."""
    op.add_column(
        "investigation_agent_runs",
        sa.Column("progress_stage", sa.String(), nullable=True),
    )
    op.add_column(
        "investigation_agent_runs",
        sa.Column("progress_message", sa.Text(), nullable=True),
    )
    op.add_column(
        "investigation_agent_runs",
        sa.Column("progress_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_investigation_agent_runs_progress_stage",
        "investigation_agent_runs",
        "progress_stage IS NULL OR progress_stage IN "
        "('starting', 'investigating_repository', 'searching_duplicates', "
        "'preparing_reproduction', 'running_browser', 'completed', 'failed')",
    )


def downgrade() -> None:
    """Remove the current persisted progress snapshot."""
    op.drop_constraint(
        "ck_investigation_agent_runs_progress_stage",
        "investigation_agent_runs",
        type_="check",
    )
    op.drop_column("investigation_agent_runs", "progress_updated_at")
    op.drop_column("investigation_agent_runs", "progress_message")
    op.drop_column("investigation_agent_runs", "progress_stage")
