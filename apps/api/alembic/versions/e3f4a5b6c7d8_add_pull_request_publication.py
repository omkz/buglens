"""add pull request publication

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-08-31 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e3f4a5b6c7d8"
down_revision: Union[str, Sequence[str], None] = "d2e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("investigation_agent_runs", sa.Column("pull_request_status", sa.String(), nullable=True))
    op.add_column("investigation_agent_runs", sa.Column("pull_request_number", sa.BigInteger(), nullable=True))
    op.add_column("investigation_agent_runs", sa.Column("pull_request_title", sa.String(), nullable=True))
    op.add_column("investigation_agent_runs", sa.Column("pull_request_url", sa.String(), nullable=True))
    op.add_column("investigation_agent_runs", sa.Column("pull_request_branch", sa.String(), nullable=True))
    op.add_column("investigation_agent_runs", sa.Column("pull_request_created_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("investigation_agent_runs", sa.Column("pull_request_publish_started_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "ck_investigation_agent_runs_pull_request_status",
        "investigation_agent_runs",
        "pull_request_status IS NULL OR pull_request_status IN "
        "('creating', 'created', 'failed', 'stale')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_investigation_agent_runs_pull_request_status",
        "investigation_agent_runs",
        type_="check",
    )
    op.drop_column("investigation_agent_runs", "pull_request_publish_started_at")
    op.drop_column("investigation_agent_runs", "pull_request_created_at")
    op.drop_column("investigation_agent_runs", "pull_request_branch")
    op.drop_column("investigation_agent_runs", "pull_request_url")
    op.drop_column("investigation_agent_runs", "pull_request_title")
    op.drop_column("investigation_agent_runs", "pull_request_number")
    op.drop_column("investigation_agent_runs", "pull_request_status")
