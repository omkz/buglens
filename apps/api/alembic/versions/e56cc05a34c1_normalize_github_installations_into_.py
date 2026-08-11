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
    op.drop_constraint(op.f('github_installations_user_id_fkey'), 'github_installations', type_='foreignkey')
    op.drop_column('github_installations', 'user_id')


def downgrade() -> None:
    """Downgrade schema.

    Best-effort only: an installation can now be linked to more than one
    user via github_connections, so there is no single user_id to backfill
    here. The column is added back nullable, with no data restored.
    """
    op.add_column('github_installations', sa.Column('user_id', sa.Uuid(), nullable=True))
    op.create_foreign_key(op.f('github_installations_user_id_fkey'), 'github_installations', 'users', ['user_id'], ['id'], ondelete='CASCADE')
    op.drop_table('github_connections')
