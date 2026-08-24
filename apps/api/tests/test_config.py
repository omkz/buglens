import pytest
from pydantic import ValidationError

from app.config import Settings


def _settings(**updates) -> Settings:
    return Settings(session_secret="test-secret", _env_file=None, **updates)


def test_session_secret_is_required(monkeypatch):
    monkeypatch.delenv("SESSION_SECRET", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    assert "session_secret" in str(exc_info.value)


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
