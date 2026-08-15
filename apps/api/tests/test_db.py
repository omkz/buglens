import asyncio
import uuid
from pathlib import Path

import pytest
from sqlalchemy import BigInteger, UniqueConstraint
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.config import get_settings
from app.db import models  # noqa: F401  (registers tables on Base.metadata)
from app.db.base import Base
from app.db.session import engine, get_db

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def test_settings_expose_a_postgres_database_url():
    settings = get_settings()
    assert settings.database_url.startswith("postgresql+psycopg://")


def test_engine_is_async_and_uses_the_configured_database_url():
    assert isinstance(engine, AsyncEngine)
    assert engine.url.drivername == "postgresql+psycopg"


def test_expected_tables_are_registered_on_the_metadata():
    assert set(Base.metadata.tables) == {
        "users",
        "github_installations",
        "github_connections",
        "projects",
    }


def test_users_table_columns_and_constraints():
    table = Base.metadata.tables["users"]

    assert list(table.primary_key.columns.keys()) == ["id"]
    assert isinstance(table.c.github_user_id.type, BigInteger)
    assert not table.c.github_user_id.nullable
    assert not table.c.github_login.nullable
    assert not table.c.created_at.nullable
    assert not table.c.updated_at.nullable
    assert table.c.created_at.type.timezone is True
    assert table.c.updated_at.type.timezone is True

    unique_indexes = {idx.name: idx for idx in table.indexes if idx.unique}
    assert "ix_users_github_user_id" in unique_indexes
    assert list(unique_indexes["ix_users_github_user_id"].columns.keys()) == [
        "github_user_id"
    ]


def test_github_installations_table_no_longer_owned_by_a_single_user():
    # GitHubInstallation must not have a direct user_id -- ownership is
    # only expressed through github_connections now.
    table = Base.metadata.tables["github_installations"]

    assert "user_id" not in table.c
    assert list(table.primary_key.columns.keys()) == ["id"]
    assert isinstance(table.c.github_installation_id.type, BigInteger)
    assert not table.c.github_installation_id.nullable
    assert not table.c.account_login.nullable
    assert table.c.created_at.type.timezone is True
    assert table.c.updated_at.type.timezone is True

    unique_indexes = {idx.name: idx for idx in table.indexes if idx.unique}
    assert "ix_github_installations_github_installation_id" in unique_indexes


def test_github_connections_table_columns_and_constraints():
    table = Base.metadata.tables["github_connections"]

    assert list(table.primary_key.columns.keys()) == ["id"]
    assert not table.c.user_id.nullable
    assert not table.c.github_installation_id.nullable
    assert table.c.created_at.type.timezone is True
    assert table.c.updated_at.type.timezone is True

    user_fks = list(table.c.user_id.foreign_keys)
    assert len(user_fks) == 1
    assert user_fks[0].target_fullname == "users.id"
    assert user_fks[0].ondelete == "CASCADE"

    installation_fks = list(table.c.github_installation_id.foreign_keys)
    assert len(installation_fks) == 1
    assert installation_fks[0].target_fullname == "github_installations.id"
    assert installation_fks[0].ondelete == "CASCADE"


def test_github_connections_unique_constraint_on_user_and_installation():
    table = Base.metadata.tables["github_connections"]

    unique_constraints = [
        c for c in table.constraints if isinstance(c, UniqueConstraint)
    ]
    matching = [
        c
        for c in unique_constraints
        if {col.name for col in c.columns} == {"user_id", "github_installation_id"}
    ]
    assert len(matching) == 1
    assert matching[0].name == "uq_github_connections_user_installation"


def test_projects_belong_to_installations_with_repository_local_uniqueness():
    table = Base.metadata.tables["projects"]

    assert list(table.primary_key.columns.keys()) == ["id"]
    assert isinstance(table.c.github_repository_id.type, BigInteger)
    assert not table.c.github_installation_id.nullable
    assert not table.c.name.nullable
    assert not table.c.github_repository_name.nullable
    assert not table.c.github_repository_full_name.nullable
    assert not table.c.default_branch.nullable
    assert table.c.app_url.nullable
    assert table.c.created_at.type.timezone is True
    assert table.c.updated_at.type.timezone is True

    installation_fks = list(table.c.github_installation_id.foreign_keys)
    assert len(installation_fks) == 1
    assert installation_fks[0].target_fullname == "github_installations.id"
    assert installation_fks[0].ondelete == "CASCADE"

    matching = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
        and {column.name for column in constraint.columns}
        == {"github_installation_id", "github_repository_id"}
    ]
    assert len(matching) == 1
    assert matching[0].name == "uq_projects_installation_repository"


def test_user_and_installation_relationships_use_passive_deletes():
    # Ensures the ORM relationships defer deletion to the DB-level
    # ON DELETE CASCADE instead of issuing per-row DELETEs themselves.
    user_rel = models.User.connections.property
    assert user_rel.passive_deletes is True
    assert user_rel.cascade.delete_orphan

    installation_rel = models.GitHubInstallation.connections.property
    assert installation_rel.passive_deletes is True
    assert installation_rel.cascade.delete_orphan

    projects_rel = models.GitHubInstallation.projects.property
    assert projects_rel.passive_deletes is True
    assert projects_rel.cascade.delete_orphan


def test_primary_keys_default_to_application_generated_uuids():
    for table_name in (
        "users",
        "github_installations",
        "github_connections",
        "projects",
    ):
        id_default = Base.metadata.tables[table_name].c.id.default
        assert id_default.is_callable
        # SQLAlchemy may wrap/copy the default spec, so check behavior
        # (produces a UUID) rather than identity against uuid.uuid4.
        assert isinstance(id_default.arg(None), uuid.UUID)


def test_no_oauth_token_columns_exist_anywhere():
    # GitHub OAuth access tokens are never persisted.
    for table in Base.metadata.tables.values():
        assert "access_token" not in table.c
        assert "refresh_token" not in table.c


def test_get_db_dependency_yields_an_async_session_and_cleans_up():
    async def _run() -> None:
        gen = get_db()
        db = await gen.__anext__()
        assert isinstance(db, AsyncSession)

        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()

    asyncio.run(_run())


def test_alembic_migration_history_has_a_single_unambiguous_head():
    # Reads migration files from disk only -- does not execute env.py or
    # require a database connection.
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(ALEMBIC_INI))
    script = ScriptDirectory.from_config(config)

    heads = script.get_heads()
    assert len(heads) == 1


def test_alembic_head_migration_chain_includes_the_hardening_revisions():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(ALEMBIC_INI))
    script = ScriptDirectory.from_config(config)

    revisions = {rev.revision for rev in script.walk_revisions()}
    assert "52dffc9eb5c6" in revisions  # widen github id columns to bigint
    assert "dd0a9d342031" in revisions  # add ON DELETE CASCADE
    assert "e56cc05a34c1" in revisions  # normalize into github_connections
    assert "a8c9d4e1f2b3" in revisions  # add installation-owned projects


def test_projects_migration_is_linear_and_supports_clean_downgrade():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(ALEMBIC_INI))
    script = ScriptDirectory.from_config(config)
    revision = script.get_revision("a8c9d4e1f2b3")
    migration_source = Path(revision.path).read_text()

    assert revision.down_revision == "e56cc05a34c1"
    assert 'op.create_table(\n        "projects"' in migration_source
    assert 'sa.Column("github_repository_id", sa.BigInteger()' in migration_source
    assert 'ondelete="CASCADE"' in migration_source
    assert 'name="uq_projects_installation_repository"' in migration_source
    assert 'op.drop_table("projects")' in migration_source


def test_normalization_migration_backfills_before_dropping_legacy_user_id():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(ALEMBIC_INI))
    script = ScriptDirectory.from_config(config)
    revision = script.get_revision("e56cc05a34c1")
    migration_source = Path(revision.path).read_text()

    assert revision.revision == "e56cc05a34c1"

    create_position = migration_source.index("op.create_table('github_connections'")
    backfill_position = migration_source.index("INSERT INTO github_connections")
    drop_position = migration_source.index(
        "op.drop_column('github_installations', 'user_id')"
    )
    assert create_position < backfill_position < drop_position

    assert "SELECT DISTINCT" in migration_source
    assert "WHERE github_installations.user_id IS NOT NULL" in migration_source
    assert "ON CONFLICT (user_id, github_installation_id) DO NOTHING" in migration_source
    assert "uq_github_connections_user_installation" in migration_source
