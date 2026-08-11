"""add on delete cascade to github installations user id fk

Revision ID: dd0a9d342031
Revises: 52dffc9eb5c6
Create Date: 2026-08-12 02:07:29.771420

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'dd0a9d342031'
down_revision: Union[str, Sequence[str], None] = '52dffc9eb5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FK_NAME = "github_installations_user_id_fkey"


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(FK_NAME, 'github_installations', type_='foreignkey')
    op.create_foreign_key(
        FK_NAME, 'github_installations', 'users', ['user_id'], ['id'],
        ondelete='CASCADE',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(FK_NAME, 'github_installations', type_='foreignkey')
    op.create_foreign_key(
        FK_NAME, 'github_installations', 'users', ['user_id'], ['id'],
    )
