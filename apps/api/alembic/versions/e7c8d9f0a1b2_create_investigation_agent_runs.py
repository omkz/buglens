"""create investigation agent runs

Revision ID: e7c8d9f0a1b2
Revises: d6b7c8e9f0a1
Create Date: 2026-08-16 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e7c8d9f0a1b2"
down_revision: Union[str, Sequence[str], None] = "d6b7c8e9f0a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create one current autonomous run per Investigation."""
    op.create_table(
        "investigation_agent_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("investigation_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("agent_model", sa.String(), nullable=False),
        sa.Column("repository_summary", postgresql.JSONB(), nullable=True),
        sa.Column("duplicate_candidates", postgresql.JSONB(), nullable=False),
        sa.Column("reproduction_plan", postgresql.JSONB(), nullable=True),
        sa.Column("generated_test", sa.Text(), nullable=True),
        sa.Column("reproduction_status", sa.String(), nullable=True),
        sa.Column("execution_result", postgresql.JSONB(), nullable=True),
        sa.Column("execution_summary", sa.Text(), nullable=True),
        sa.Column("execution_error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_investigation_agent_runs_status",
        ),
        sa.CheckConstraint(
            "reproduction_status IS NULL OR reproduction_status IN "
            "('reproduced', 'not_reproduced', 'blocked')",
            name="ck_investigation_agent_runs_reproduction_status",
        ),
        sa.ForeignKeyConstraint(
            ["investigation_id"], ["investigations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "investigation_id",
            name="uq_investigation_agent_runs_investigation_id",
        ),
    )


def downgrade() -> None:
    """Drop autonomous Investigation results."""
    op.drop_table("investigation_agent_runs")
