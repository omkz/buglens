import asyncio
import uuid
from pathlib import Path

import pytest
from sqlalchemy import BigInteger
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
    assert set(Base.metadata.tables) == {"users", "github_installations"}


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


def test_github_installations_table_columns_and_constraints():
    table = Base.metadata.tables["github_installations"]

    assert list(table.primary_key.columns.keys()) == ["id"]
    assert isinstance(table.c.github_installation_id.type, BigInteger)
    assert not table.c.user_id.nullable
    assert not table.c.github_installation_id.nullable
    assert not table.c.account_login.nullable
    assert table.c.created_at.type.timezone is True
    assert table.c.updated_at.type.timezone is True

    unique_indexes = {idx.name: idx for idx in table.indexes if idx.unique}
    assert "ix_github_installations_github_installation_id" in unique_indexes


def test_github_installations_user_id_fk_cascades_on_delete():
    table = Base.metadata.tables["github_installations"]

    fks = list(table.c.user_id.foreign_keys)
    assert len(fks) == 1
    assert fks[0].target_fullname == "users.id"
    assert fks[0].ondelete == "CASCADE"


def test_user_installations_relationship_uses_passive_deletes():
    # Ensures the ORM relationship defers deletion to the DB-level
    # ON DELETE CASCADE instead of issuing per-row DELETEs itself.
    relationship = models.User.installations.property
    assert relationship.passive_deletes is True
    assert relationship.cascade.delete_orphan


def test_primary_keys_default_to_application_generated_uuids():
    for table_name in ("users", "github_installations"):
        id_default = Base.metadata.tables[table_name].c.id.default
        assert id_default.is_callable
        # SQLAlchemy may wrap/copy the default spec, so check behavior
        # (produces a UUID) rather than identity against uuid.uuid4.
        assert isinstance(id_default.arg(None), uuid.UUID)


def test_no_oauth_token_columns_exist_yet():
    # This step intentionally doesn't persist GitHub OAuth tokens.
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
