"""add agent run attempt id

Revision ID: b0f1c2d3e4f5
Revises: a9e0f1b2c3d4
Create Date: 2026-08-21 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b0f1c2d3e4f5"
down_revision: Union[str, Sequence[str], None] = "a9e0f1b2c3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add nullable identity for one concrete execution attempt."""
    op.add_column(
        "investigation_agent_runs",
        sa.Column("run_attempt_id", sa.Uuid(), nullable=True),
    )


def downgrade() -> None:
    """Remove the execution attempt identity."""
    op.drop_column("investigation_agent_runs", "run_attempt_id")
