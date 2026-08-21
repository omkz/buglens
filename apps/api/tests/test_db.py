import asyncio
import uuid
from pathlib import Path

import pytest
from sqlalchemy import BigInteger, Boolean, CheckConstraint, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
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
        "investigations",
        "investigation_evidence",
        "investigation_analyses",
        "investigation_agent_runs",
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


def test_investigations_belong_to_projects_with_pending_default():
    table = Base.metadata.tables["investigations"]

    assert list(table.primary_key.columns.keys()) == ["id"]
    assert not table.c.project_id.nullable
    assert not table.c.title.nullable
    assert isinstance(table.c.description.type, Text)
    assert table.c.description.nullable
    assert not table.c.status.nullable
    assert table.c.status.server_default.arg == "pending"
    assert table.c.created_at.type.timezone is True
    assert table.c.updated_at.type.timezone is True

    project_fks = list(table.c.project_id.foreign_keys)
    assert len(project_fks) == 1
    assert project_fks[0].target_fullname == "projects.id"
    assert project_fks[0].ondelete == "CASCADE"

    status_constraints = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    ]
    assert len(status_constraints) == 1
    assert status_constraints[0].name == "ck_investigations_status"
    for status in ("pending", "running", "completed", "failed"):
        assert status in str(status_constraints[0].sqltext)

    project_indexes = {index.name: index for index in table.indexes}
    assert "ix_investigations_project_id" in project_indexes


def test_investigation_evidence_metadata_and_constraints():
    table = Base.metadata.tables["investigation_evidence"]

    assert list(table.primary_key.columns.keys()) == ["id"]
    assert not table.c.investigation_id.nullable
    assert not table.c.kind.nullable
    assert table.c.mime_type.nullable
    assert table.c.filename.nullable
    assert table.c.storage_key.nullable
    assert isinstance(table.c.size_bytes.type, BigInteger)
    assert table.c.size_bytes.nullable
    assert isinstance(table.c.text_content.type, Text)
    assert table.c.text_content.nullable
    assert table.c.created_at.type.timezone is True
    assert table.c.updated_at.type.timezone is True

    investigation_fks = list(table.c.investigation_id.foreign_keys)
    assert len(investigation_fks) == 1
    assert investigation_fks[0].target_fullname == "investigations.id"
    assert investigation_fks[0].ondelete == "CASCADE"

    kind_constraints = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    ]
    assert len(kind_constraints) == 1
    assert kind_constraints[0].name == "ck_investigation_evidence_kind"
    assert "recording" in str(kind_constraints[0].sqltext)
    assert "logs" in str(kind_constraints[0].sqltext)

    indexes = {index.name: index for index in table.indexes}
    assert "ix_investigation_evidence_investigation_id" in indexes


def test_investigation_analysis_metadata_and_one_per_investigation():
    table = Base.metadata.tables["investigation_analyses"]

    assert list(table.primary_key.columns.keys()) == ["id"]
    assert not table.c.investigation_id.nullable
    assert not table.c.model_name.nullable
    assert isinstance(table.c.summary.type, Text)
    assert isinstance(table.c.observed_behavior.type, Text)
    assert isinstance(table.c.expected_behavior.type, Text)
    assert table.c.expected_behavior.nullable
    for column_name in (
        "reproduction_steps",
        "error_signals",
        "suspected_components",
        "missing_information",
    ):
        assert isinstance(table.c[column_name].type, JSONB)
        assert not table.c[column_name].nullable
    assert isinstance(table.c.needs_more_information.type, Boolean)
    assert not table.c.needs_more_information.nullable

    investigation_fks = list(table.c.investigation_id.foreign_keys)
    assert len(investigation_fks) == 1
    assert investigation_fks[0].target_fullname == "investigations.id"
    assert investigation_fks[0].ondelete == "CASCADE"

    unique = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    ]
    assert len(unique) == 1
    assert unique[0].name == "uq_investigation_analyses_investigation_id"
    assert [column.name for column in unique[0].columns] == ["investigation_id"]

    confidence = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    ]
    assert len(confidence) == 1
    assert confidence[0].name == "ck_investigation_analyses_confidence"
    for value in ("low", "medium", "high"):
        assert value in str(confidence[0].sqltext)

    assert set(table.c.keys()).isdisjoint(
        {
            "gemini_api_key",
            "gemini_file_name",
            "gemini_file_uri",
            "raw_response",
        }
    )


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

    investigations_rel = models.Project.investigations.property
    assert investigations_rel.passive_deletes is True
    assert investigations_rel.cascade.delete_orphan

    evidence_rel = models.Investigation.evidence_items.property
    assert evidence_rel.passive_deletes is True
    assert evidence_rel.cascade.delete_orphan

    analysis_rel = models.Investigation.analysis.property
    assert analysis_rel.passive_deletes is True
    assert analysis_rel.cascade.delete_orphan
    assert analysis_rel.uselist is False

    agent_run_rel = models.Investigation.agent_run.property
    assert agent_run_rel.passive_deletes is True
    assert agent_run_rel.cascade.delete_orphan
    assert agent_run_rel.uselist is False


def test_primary_keys_default_to_application_generated_uuids():
    for table_name in (
        "users",
        "github_installations",
        "github_connections",
        "projects",
        "investigations",
        "investigation_evidence",
        "investigation_analyses",
        "investigation_agent_runs",
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
        assert "installation_token" not in table.c
        assert "gemini_api_key" not in table.c


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
    assert "b4f1c2d3e4a5" in revisions  # add Project-owned investigations
    assert "c5a6b7d8e9f0" in revisions  # add Investigation-owned evidence
    assert "d6b7c8e9f0a1" in revisions  # add current structured analysis
    assert "e7c8d9f0a1b2" in revisions  # add autonomous investigation runs
    assert "f8d9e0a1b2c3" in revisions  # add GitHub issue publication
    assert "a9e0f1b2c3d4" in revisions  # add persisted AgentRun progress


def test_agent_run_progress_schema_and_migration_are_constrained_and_reversible():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    table = Base.metadata.tables["investigation_agent_runs"]
    assert table.c.progress_stage.nullable
    assert isinstance(table.c.progress_message.type, Text)
    assert table.c.progress_updated_at.type.timezone is True
    constraint = next(
        item
        for item in table.constraints
        if item.name == "ck_investigation_agent_runs_progress_stage"
    )
    for stage in models.AgentRunProgressStage:
        assert stage.value in str(constraint.sqltext)

    config = Config(str(ALEMBIC_INI))
    script = ScriptDirectory.from_config(config)
    revision = script.get_revision("a9e0f1b2c3d4")
    source = Path(revision.path).read_text()
    assert revision.down_revision == "f8d9e0a1b2c3"
    assert 'op.drop_column("investigation_agent_runs", "progress_stage")' in source
    assert "ck_investigation_agent_runs_progress_stage" in source


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


def test_investigations_migration_is_linear_and_supports_clean_downgrade():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(ALEMBIC_INI))
    script = ScriptDirectory.from_config(config)
    revision = script.get_revision("b4f1c2d3e4a5")
    migration_source = Path(revision.path).read_text()

    assert revision.down_revision == "a8c9d4e1f2b3"
    assert 'op.create_table(\n        "investigations"' in migration_source
    assert 'sa.Column("project_id", sa.Uuid()' in migration_source
    assert 'server_default=sa.text("\'pending\'")' in migration_source
    assert 'name="ck_investigations_status"' in migration_source
    assert 'ondelete="CASCADE"' in migration_source
    assert 'op.drop_table("investigations")' in migration_source


def test_evidence_migration_is_linear_and_supports_clean_downgrade():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(ALEMBIC_INI))
    script = ScriptDirectory.from_config(config)
    revision = script.get_revision("c5a6b7d8e9f0")
    migration_source = Path(revision.path).read_text()

    assert revision.down_revision == "b4f1c2d3e4a5"
    assert 'op.create_table(\n        "investigation_evidence"' in migration_source
    assert 'sa.Column("size_bytes", sa.BigInteger()' in migration_source
    assert 'name="ck_investigation_evidence_kind"' in migration_source
    assert 'ondelete="CASCADE"' in migration_source
    assert 'op.drop_table("investigation_evidence")' in migration_source


def test_analysis_migration_is_linear_and_supports_clean_downgrade():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(ALEMBIC_INI))
    script = ScriptDirectory.from_config(config)
    revision = script.get_revision("d6b7c8e9f0a1")
    migration_source = Path(revision.path).read_text()

    assert revision.down_revision == "c5a6b7d8e9f0"
    assert 'op.create_table(\n        "investigation_analyses"' in migration_source
    assert 'postgresql.JSONB()' in migration_source
    assert 'name="uq_investigation_analyses_investigation_id"' in migration_source
    assert 'name="ck_investigation_analyses_confidence"' in migration_source
    assert 'ondelete="CASCADE"' in migration_source
    assert 'op.drop_table("investigation_analyses")' in migration_source


def test_agent_run_migration_is_linear_and_supports_clean_downgrade():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(ALEMBIC_INI))
    script = ScriptDirectory.from_config(config)
    revision = script.get_revision("e7c8d9f0a1b2")
    migration_source = Path(revision.path).read_text()

    assert revision.down_revision == "d6b7c8e9f0a1"
    assert 'op.create_table(\n        "investigation_agent_runs"' in migration_source
    assert 'postgresql.JSONB()' in migration_source
    assert 'name="uq_investigation_agent_runs_investigation_id"' in migration_source
    assert 'name="ck_investigation_agent_runs_status"' in migration_source
    assert 'ondelete="CASCADE"' in migration_source
    assert 'op.drop_table("investigation_agent_runs")' in migration_source


def test_github_issue_publication_migration_is_linear_and_reversible():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(ALEMBIC_INI))
    script = ScriptDirectory.from_config(config)
    revision = script.get_revision("f8d9e0a1b2c3")
    migration_source = Path(revision.path).read_text()

    assert revision.down_revision == "e7c8d9f0a1b2"
    assert 'sa.Column("github_issue_status"' in migration_source
    assert 'sa.Column("github_issue_number", sa.BigInteger()' in migration_source
    assert '"ck_investigation_agent_runs_github_issue_status"' in migration_source
    assert 'op.drop_column("investigation_agent_runs", "github_issue_status")' in migration_source


def test_agent_run_model_has_one_current_run_and_safe_status_constraints():
    table = Base.metadata.tables["investigation_agent_runs"]

    assert list(table.primary_key.columns.keys()) == ["id"]
    assert isinstance(table.c.repository_summary.type, JSONB)
    assert isinstance(table.c.duplicate_candidates.type, JSONB)
    assert isinstance(table.c.reproduction_plan.type, JSONB)
    assert isinstance(table.c.execution_result.type, JSONB)
    assert isinstance(table.c.generated_test.type, Text)
    assert table.c.started_at.type.timezone is True
    assert table.c.completed_at.type.timezone is True
    assert table.c.github_issue_created_at.type.timezone is True
    assert table.c.github_issue_publish_started_at.type.timezone is True
    assert isinstance(table.c.github_issue_number.type, BigInteger)
    assert list(table.c.investigation_id.foreign_keys)[0].ondelete == "CASCADE"

    unique_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    check_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "uq_investigation_agent_runs_investigation_id" in unique_names
    assert "ck_investigation_agent_runs_status" in check_names
    assert "ck_investigation_agent_runs_github_issue_status" in check_names
    assert "ck_investigation_agent_runs_reproduction_status" in check_names


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
