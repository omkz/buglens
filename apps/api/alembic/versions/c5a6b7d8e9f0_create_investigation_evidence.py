"""create investigation evidence

Revision ID: c5a6b7d8e9f0
Revises: b4f1c2d3e4a5
Create Date: 2026-08-16 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c5a6b7d8e9f0"
down_revision: Union[str, Sequence[str], None] = "b4f1c2d3e4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create Investigation-owned evidence metadata."""
    op.create_table(
        "investigation_evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("investigation_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("mime_type", sa.String(), nullable=True),
        sa.Column("filename", sa.String(), nullable=True),
        sa.Column("storage_key", sa.String(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("text_content", sa.Text(), nullable=True),
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
            "kind IN ('recording', 'logs')",
            name="ck_investigation_evidence_kind",
        ),
        sa.ForeignKeyConstraint(
            ["investigation_id"],
            ["investigations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_investigation_evidence_investigation_id"),
        "investigation_evidence",
        ["investigation_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop Investigation-owned evidence metadata."""
    op.drop_index(
        op.f("ix_investigation_evidence_investigation_id"),
        table_name="investigation_evidence",
    )
    op.drop_table("investigation_evidence")
