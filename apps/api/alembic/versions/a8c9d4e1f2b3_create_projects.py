"""create projects

Revision ID: a8c9d4e1f2b3
Revises: e56cc05a34c1
Create Date: 2026-08-16 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a8c9d4e1f2b3"
down_revision: Union[str, Sequence[str], None] = "e56cc05a34c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create installation-owned Buglensa projects."""
    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("github_installation_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("github_repository_id", sa.BigInteger(), nullable=False),
        sa.Column("github_repository_name", sa.String(), nullable=False),
        sa.Column("github_repository_full_name", sa.String(), nullable=False),
        sa.Column("default_branch", sa.String(), nullable=False),
        sa.Column("app_url", sa.String(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["github_installation_id"],
            ["github_installations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "github_installation_id",
            "github_repository_id",
            name="uq_projects_installation_repository",
        ),
    )


def downgrade() -> None:
    """Drop installation-owned Buglensa projects."""
    op.drop_table("projects")
