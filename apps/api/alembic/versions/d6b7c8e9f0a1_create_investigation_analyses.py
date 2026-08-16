"""create investigation analyses

Revision ID: d6b7c8e9f0a1
Revises: c5a6b7d8e9f0
Create Date: 2026-08-16 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "d6b7c8e9f0a1"
down_revision: Union[str, Sequence[str], None] = "c5a6b7d8e9f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create one current structured analysis per Investigation."""
    op.create_table(
        "investigation_analyses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("investigation_id", sa.Uuid(), nullable=False),
        sa.Column("model_name", sa.String(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("observed_behavior", sa.Text(), nullable=False),
        sa.Column("expected_behavior", sa.Text(), nullable=True),
        sa.Column("reproduction_steps", postgresql.JSONB(), nullable=False),
        sa.Column("error_signals", postgresql.JSONB(), nullable=False),
        sa.Column("suspected_components", postgresql.JSONB(), nullable=False),
        sa.Column("confidence", sa.String(), nullable=False),
        sa.Column("needs_more_information", sa.Boolean(), nullable=False),
        sa.Column("missing_information", postgresql.JSONB(), nullable=False),
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
            "confidence IN ('low', 'medium', 'high')",
            name="ck_investigation_analyses_confidence",
        ),
        sa.ForeignKeyConstraint(
            ["investigation_id"],
            ["investigations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "investigation_id",
            name="uq_investigation_analyses_investigation_id",
        ),
    )


def downgrade() -> None:
    """Drop persisted Investigation analyses."""
    op.drop_table("investigation_analyses")
