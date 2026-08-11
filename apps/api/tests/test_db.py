import uuid

import pytest
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import models  # noqa: F401  (registers tables on Base.metadata)
from app.db.base import Base
from app.db.session import engine, get_db


def test_settings_expose_a_postgres_database_url():
    settings = get_settings()
    assert settings.database_url.startswith("postgresql+psycopg://")


def test_engine_uses_the_configured_database_url():
    assert engine.url.drivername == "postgresql+psycopg"


def test_expected_tables_are_registered_on_the_metadata():
    assert set(Base.metadata.tables) == {"users", "github_installations"}


def test_users_table_columns_and_constraints():
    table = Base.metadata.tables["users"]

    assert list(table.primary_key.columns.keys()) == ["id"]
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
    assert not table.c.user_id.nullable
    assert not table.c.github_installation_id.nullable
    assert not table.c.account_login.nullable
    assert table.c.created_at.type.timezone is True
    assert table.c.updated_at.type.timezone is True

    fk_targets = {fk.target_fullname for fk in table.c.user_id.foreign_keys}
    assert fk_targets == {"users.id"}

    unique_indexes = {idx.name: idx for idx in table.indexes if idx.unique}
    assert "ix_github_installations_github_installation_id" in unique_indexes


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


def test_get_db_dependency_yields_a_session_and_cleans_up():
    gen = get_db()
    db = next(gen)
    assert isinstance(db, Session)

    with pytest.raises(StopIteration):
        next(gen)
