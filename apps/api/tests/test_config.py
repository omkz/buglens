import pytest
from pydantic import ValidationError

from app.config import DatabaseSettings, Settings


def _settings(**updates) -> Settings:
    return Settings(session_secret="test-secret", _env_file=None, **updates)


def test_session_secret_is_required(monkeypatch):
    monkeypatch.delenv("SESSION_SECRET", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    assert "session_secret" in str(exc_info.value)


def test_database_settings_do_not_require_runtime_session_secret(monkeypatch):
    monkeypatch.delenv("SESSION_SECRET", raising=False)

    settings = DatabaseSettings(
        database_url="postgresql+psycopg://user:password@localhost/buglens",
        _env_file=None,
    )

    assert settings.database_url.endswith("@localhost/buglens")


def test_database_pool_and_local_development_defaults(monkeypatch):
    for name in (
        "DATABASE_POOL_SIZE",
        "DATABASE_MAX_OVERFLOW",
        "DATABASE_POOL_TIMEOUT_SECONDS",
        "DATABASE_POOL_RECYCLE_SECONDS",
        "EVIDENCE_STORAGE_BACKEND",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = _settings()

    assert settings.database_pool_size == 5
    assert settings.database_max_overflow == 2
    assert settings.database_pool_timeout_seconds == 30
    assert settings.database_pool_recycle_seconds == 1800
    assert settings.evidence_storage_backend == "local"
    assert str(settings.evidence_storage_dir) == ".data/evidence"
    assert settings.playwright_allow_private_network is False
    assert settings.agent_run_timeout_seconds == 180
    assert settings.fix_validation_allow_host_execution is False
    assert settings.trusted_fix_validation_repositories == frozenset()
    assert settings.frontend_base_url == "http://localhost:3000"
    assert settings.backend_base_url == "http://localhost:3000/api"
    assert (
        settings.github_callback_url
        == "http://localhost:3000/api/github/oauth/callback"
    )


def test_fix_validation_trusted_repositories_are_explicit(monkeypatch):
    monkeypatch.delenv("FIX_VALIDATION_ALLOW_HOST_EXECUTION", raising=False)
    monkeypatch.delenv("FIX_VALIDATION_TRUSTED_REPOSITORIES", raising=False)

    settings = _settings(
        fix_validation_allow_host_execution=True,
        fix_validation_trusted_repositories=(
            "omkz/buglens-demo-target, trusted/example "
        ),
    )

    assert settings.fix_validation_allow_host_execution is True
    assert settings.trusted_fix_validation_repositories == frozenset(
        {"omkz/buglens-demo-target", "trusted/example"}
    )


def test_vertex_ai_environment_configuration(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "orbital-wharf-427808-p5")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    settings = _settings()

    assert settings.google_cloud_project == "orbital-wharf-427808-p5"
    assert settings.google_cloud_location == "global"
    assert not hasattr(settings, "gemini_api_key")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("database_pool_size", 0),
        ("database_pool_size", 51),
        ("database_max_overflow", -1),
        ("database_max_overflow", 51),
        ("database_pool_timeout_seconds", 0),
        ("database_pool_timeout_seconds", 121),
        ("database_pool_recycle_seconds", 59),
    ],
)
def test_invalid_database_pool_configuration_is_rejected(field, value):
    with pytest.raises(ValidationError, match=field):
        _settings(**{field: value})


@pytest.mark.parametrize("value", [0, -1, 901])
def test_invalid_agent_run_timeout_is_rejected(value):
    with pytest.raises(ValidationError, match="agent_run_timeout_seconds"):
        _settings(agent_run_timeout_seconds=value)
