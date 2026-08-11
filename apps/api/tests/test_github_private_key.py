"""Tests for resolving the GitHub App private key from either a file path
(GITHUB_PRIVATE_KEY_PATH) or an inline, possibly-escaped env value
(GITHUB_PRIVATE_KEY).

Uses temporary files only -- no real .pem fixture is added to the repo.
"""

from __future__ import annotations

import os
import stat
import uuid
from unittest.mock import Mock

import pytest

from app.integrations.github import client as github_client
from app.integrations.github.keys import resolve_private_key

FAKE_PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "FAKEKEYCONTENT\n"
    "-----END RSA PRIVATE KEY-----\n"
)

_running_as_root = hasattr(os, "geteuid") and os.geteuid() == 0


# --- resolve_private_key: source precedence -----------------------------


def test_resolves_from_path_when_configured(tmp_path):
    key_file = tmp_path / "buglens.pem"
    key_file.write_text(FAKE_PEM, encoding="utf-8")

    resolved = resolve_private_key(private_key_path=str(key_file), private_key="")

    assert resolved == FAKE_PEM


def test_falls_back_to_inline_env_key_when_no_path_configured():
    resolved = resolve_private_key(private_key_path="", private_key=FAKE_PEM)

    assert resolved == FAKE_PEM


def test_escaped_newline_inline_key_is_preserved_for_create_app_jwt_to_normalize():
    escaped = FAKE_PEM.replace("\n", "\\n")

    resolved = resolve_private_key(private_key_path="", private_key=escaped)

    # resolve_private_key itself doesn't normalize escaped newlines --
    # create_app_jwt already does that (existing, unchanged behavior).
    assert resolved == escaped
    assert "\\n" in resolved


def test_path_takes_precedence_over_inline_key_when_both_configured(tmp_path):
    key_file = tmp_path / "buglens.pem"
    key_file.write_text(FAKE_PEM, encoding="utf-8")
    inline_key = (
        "-----BEGIN RSA PRIVATE KEY-----\nSHOULD-NOT-BE-USED"
        "\n-----END RSA PRIVATE KEY-----\n"
    )

    resolved = resolve_private_key(
        private_key_path=str(key_file), private_key=inline_key
    )

    assert resolved == FAKE_PEM


# --- resolve_private_key: safe error handling ---------------------------


def test_missing_file_raises_safe_error(tmp_path):
    missing_path = tmp_path / "does-not-exist.pem"

    with pytest.raises(github_client.GitHubAPIError) as exc_info:
        resolve_private_key(private_key_path=str(missing_path), private_key="")

    message = str(exc_info.value)
    assert str(missing_path) not in message
    assert "does-not-exist" not in message


@pytest.mark.skipif(_running_as_root, reason="permission bits don't block root")
def test_unreadable_file_raises_safe_error(tmp_path):
    key_file = tmp_path / "buglens.pem"
    key_file.write_text(FAKE_PEM, encoding="utf-8")
    key_file.chmod(0)

    try:
        with pytest.raises(github_client.GitHubAPIError) as exc_info:
            resolve_private_key(private_key_path=str(key_file), private_key="")
        message = str(exc_info.value)
        assert FAKE_PEM not in message
        assert str(key_file) not in message
    finally:
        key_file.chmod(stat.S_IRUSR | stat.S_IWUSR)


def test_neither_path_nor_inline_key_configured_raises_safe_error():
    with pytest.raises(github_client.GitHubAPIError) as exc_info:
        resolve_private_key(private_key_path="", private_key="")

    assert "not configured" in str(exc_info.value).lower()


@pytest.mark.skipif(_running_as_root, reason="permission bits don't block root")
def test_error_messages_never_contain_key_contents(tmp_path):
    key_file = tmp_path / "buglens.pem"
    key_file.write_text(FAKE_PEM, encoding="utf-8")
    key_file.chmod(0)

    try:
        with pytest.raises(github_client.GitHubAPIError) as exc_info:
            resolve_private_key(private_key_path=str(key_file), private_key=FAKE_PEM)
        assert "FAKEKEYCONTENT" not in str(exc_info.value)
    finally:
        key_file.chmod(stat.S_IRUSR | stat.S_IWUSR)


# --- integration with create_app_jwt / the /repositories route ----------


def test_path_contents_are_what_gets_passed_to_jwt_generation(tmp_path, monkeypatch):
    key_file = tmp_path / "buglens.pem"
    key_file.write_text(FAKE_PEM, encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_encode(claims, key, algorithm):
        captured["key"] = key
        return "fake.jwt.token"

    monkeypatch.setattr(github_client.jwt, "encode", fake_encode)

    resolved = resolve_private_key(private_key_path=str(key_file), private_key="")
    token = github_client.create_app_jwt(client_id="Iv1.test", private_key=resolved)

    assert token == "fake.jwt.token"
    assert captured["key"] == FAKE_PEM


def test_repositories_route_resolves_private_key_from_configured_path(
    tmp_path, monkeypatch
):
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.integrations.github import routes
    from app.integrations.github.repository import PersistedGitHubConnection
    from app.main import app
    from tests.test_github_repositories import _signed_session_cookie

    key_file = tmp_path / "buglens.pem"
    key_file.write_text(FAKE_PEM, encoding="utf-8")

    connection = PersistedGitHubConnection(
        connection_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        installation_id=uuid.uuid4(),
        github_installation_id=555,
        account_login="octo-org",
    )
    captured: dict[str, object] = {}

    async def lookup(*args, **kwargs):
        return connection

    def create_jwt(*, client_id, private_key):
        captured["private_key"] = private_key
        return "fake-app-jwt"

    async def create_token(*, installation_id, app_jwt):
        return "fake-installation-token"

    async def list_repositories(token):
        return []

    monkeypatch.setattr(routes, "get_connection_by_id", lookup)
    monkeypatch.setattr(github_client, "create_app_jwt", create_jwt)
    monkeypatch.setattr(
        github_client, "create_installation_access_token", create_token
    )
    monkeypatch.setattr(
        github_client, "list_installation_repositories", list_repositories
    )

    settings = get_settings().model_copy(
        update={
            "github_private_key_path": str(key_file),
            # Deliberately different from the file, to prove the path wins.
            "github_private_key": "should-not-be-used",
        }
    )
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        client = TestClient(app)
        client.cookies.set(
            "buglens_session",
            _signed_session_cookie(str(connection.connection_id)),
        )
        response = client.get("/github/repositories")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert captured["private_key"] == FAKE_PEM
    assert FAKE_PEM not in response.text
    assert "should-not-be-used" not in response.text


def test_repositories_route_returns_safe_502_when_private_key_unconfigured(
    monkeypatch,
):
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.integrations.github import routes
    from app.integrations.github.repository import PersistedGitHubConnection
    from app.main import app
    from tests.test_github_repositories import _signed_session_cookie

    connection = PersistedGitHubConnection(
        connection_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        installation_id=uuid.uuid4(),
        github_installation_id=555,
        account_login="octo-org",
    )

    async def lookup(*args, **kwargs):
        return connection

    test_logger = Mock()
    monkeypatch.setattr(routes, "get_connection_by_id", lookup)
    monkeypatch.setattr(routes, "logger", test_logger)

    settings = get_settings().model_copy(
        update={"github_private_key_path": "", "github_private_key": ""}
    )
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        client = TestClient(app)
        client.cookies.set(
            "buglens_session",
            _signed_session_cookie(str(connection.connection_id)),
        )
        response = client.get("/github/repositories")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502
    assert response.json() == {"detail": "Unable to load GitHub repositories."}
    test_logger.exception.assert_called_once_with(
        "github_repositories_api_failed",
        installation_id=connection.github_installation_id,
    )
