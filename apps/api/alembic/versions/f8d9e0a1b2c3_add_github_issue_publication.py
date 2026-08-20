"""add github issue publication

Revision ID: f8d9e0a1b2c3
Revises: e7c8d9f0a1b2
Create Date: 2026-08-20 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f8d9e0a1b2c3"
down_revision: Union[str, Sequence[str], None] = "e7c8d9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add idempotent GitHub issue publication state to agent runs."""
    op.add_column(
        "investigation_agent_runs",
        sa.Column("github_issue_status", sa.String(), nullable=True),
    )
    op.add_column(
        "investigation_agent_runs",
        sa.Column("github_issue_number", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "investigation_agent_runs",
        sa.Column("github_issue_title", sa.String(), nullable=True),
    )
    op.add_column(
        "investigation_agent_runs",
        sa.Column("github_issue_url", sa.String(), nullable=True),
    )
    op.add_column(
        "investigation_agent_runs",
        sa.Column(
            "github_issue_created_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "investigation_agent_runs",
        sa.Column(
            "github_issue_publish_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_investigation_agent_runs_github_issue_status",
        "investigation_agent_runs",
        "github_issue_status IS NULL OR github_issue_status IN "
        "('creating', 'created', 'failed')",
    )


def downgrade() -> None:
    """Remove GitHub issue publication state."""
    op.drop_constraint(
        "ck_investigation_agent_runs_github_issue_status",
        "investigation_agent_runs",
        type_="check",
    )
    op.drop_column("investigation_agent_runs", "github_issue_publish_started_at")
    op.drop_column("investigation_agent_runs", "github_issue_created_at")
    op.drop_column("investigation_agent_runs", "github_issue_url")
    op.drop_column("investigation_agent_runs", "github_issue_title")
    op.drop_column("investigation_agent_runs", "github_issue_number")
    op.drop_column("investigation_agent_runs", "github_issue_status")
