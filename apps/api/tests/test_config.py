import pytest
from pydantic import ValidationError

from app.config import Settings


def test_session_secret_is_required(monkeypatch):
    monkeypatch.delenv("SESSION_SECRET", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    assert "session_secret" in str(exc_info.value)
