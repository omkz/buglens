"""normalize github installations into github connections

Revision ID: e56cc05a34c1
Revises: dd0a9d342031
Create Date: 2026-08-12 02:53:16.360992

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e56cc05a34c1'
down_revision: Union[str, Sequence[str], None] = 'dd0a9d342031'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FK_NAME = "github_installations_user_id_fkey"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('github_connections',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('github_installation_id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['github_installation_id'], ['github_installations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'github_installation_id', name='uq_github_connections_user_installation')
    )

    # Expand -> backfill -> contract: preserve every legacy installation
    # owner before removing the old direct relationship. DISTINCT plus the
    # table's unique constraint prevents duplicate user/installation pairs;
    # ON CONFLICT is an additional guard if the source shape changes while
    # this unreleased migration is still being developed.
    op.execute(
        sa.text(
            """
            INSERT INTO github_connections (
                id,
                user_id,
                github_installation_id,
                created_at,
                updated_at
            )
            SELECT
                gen_random_uuid(),
                legacy_relationships.user_id,
                legacy_relationships.github_installation_id,
                now(),
                now()
            FROM (
                SELECT DISTINCT
                    github_installations.user_id,
                    github_installations.id AS github_installation_id
                FROM github_installations
                WHERE github_installations.user_id IS NOT NULL
            ) AS legacy_relationships
            ON CONFLICT (user_id, github_installation_id) DO NOTHING
            """
        )
    )

    op.drop_constraint(FK_NAME, 'github_installations', type_='foreignkey')
    op.drop_column('github_installations', 'user_id')


def downgrade() -> None:
    """Restore the legacy one-user-per-installation schema safely."""

    # Old schema can only represent exactly one user per installation.
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM github_installations
                    LEFT JOIN github_connections
                      ON github_connections.github_installation_id =
                         github_installations.id
                    GROUP BY github_installations.id
                    HAVING count(DISTINCT github_connections.user_id) <> 1
                ) THEN
                    RAISE EXCEPTION
                        'Cannot downgrade e56cc05a34c1: each GitHub installation must have exactly one user connection';
                END IF;
            END
            $$;
            """
        )
    )

    op.add_column(
        "github_installations",
        sa.Column("user_id", sa.Uuid(), nullable=True),
    )

    op.execute(
        sa.text(
            """
            UPDATE github_installations
            SET user_id = github_connections.user_id
            FROM github_connections
            WHERE github_installations.id =
                  github_connections.github_installation_id
            """
        )
    )

    # Defensive verification before restoring NOT NULL.
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM github_installations
                    WHERE user_id IS NULL
                ) THEN
                    RAISE EXCEPTION
                        'Cannot downgrade e56cc05a34c1: failed to restore required installation owner';
                END IF;
            END
            $$;
            """
        )
    )

    op.alter_column(
        "github_installations",
        "user_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )

    op.create_foreign_key(
        FK_NAME,
        "github_installations",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_table("github_connections")
