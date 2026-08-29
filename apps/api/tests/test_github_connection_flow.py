"""OAuth-first GitHub App connection-flow tests."""

from __future__ import annotations

import json
import uuid
from base64 import b64decode
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import itsdangerous
import pytest

from app.config import get_settings
from app.db.session import get_db
from app.integrations.github import client as github_client


class FakeDatabaseSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


@pytest.fixture
def callback_app():
    from app.main import app

    database = FakeDatabaseSession()

    async def override_database():
        yield database

    app.dependency_overrides[get_db] = override_database
    try:
        yield app, database
    finally:
        app.dependency_overrides.pop(get_db, None)


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query)


def _session_payload(client) -> dict[str, str]:
    cookie = client.cookies["buglens_session"]
    signer = itsdangerous.TimestampSigner(get_settings().session_secret)
    encoded = signer.unsign(cookie)
    return json.loads(b64decode(encoded))


def _stub_oauth(monkeypatch, installations):
    fake_token = "ghu_transient_user_token"

    async def exchange(**kwargs):
        assert kwargs == {
            "client_id": get_settings().github_client_id,
            "client_secret": get_settings().github_client_secret,
            "code": kwargs["code"],
            "redirect_uri": get_settings().github_callback_url,
        }
        return fake_token

    async def fetch_user(access_token):
        assert access_token == fake_token
        return github_client.GitHubUser(id=12345, login="octocat")

    async def fetch_installations(access_token):
        assert access_token == fake_token
        if callable(installations):
            return installations()
        return installations

    monkeypatch.setattr(github_client, "exchange_code_for_token", exchange)
    monkeypatch.setattr(github_client, "fetch_authenticated_user", fetch_user)
    monkeypatch.setattr(
        github_client,
        "fetch_user_installations",
        fetch_installations,
    )
    return fake_token


def _stub_persistence(monkeypatch, installation_id: int):
    from app.integrations.github import routes

    captured: dict[str, object] = {}
    connection_id = uuid.uuid4()

    async def persist(database, **kwargs):
        captured["database"] = database
        captured.update(kwargs)
        return SimpleNamespace(
            connection_id=connection_id,
            github_installation_id=installation_id,
        )

    monkeypatch.setattr(routes, "persist_github_connection", persist)
    return captured, connection_id


def test_connect_url_starts_user_oauth_without_requesting_scopes():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    response = client.get("/api/github/connect-url")

    assert response.status_code == 200
    url = response.json()["url"]
    assert urlsplit(url)._replace(query="", fragment="").geturl() == (
        "https://github.com/login/oauth/authorize"
    )
    query = _query(url)
    assert query == {
        "client_id": [get_settings().github_client_id],
        "redirect_uri": [get_settings().github_callback_url],
        "state": [query["state"][0]],
    }
    assert query["state"][0]
    assert "scope" not in query
    assert _session_payload(client)["github_oauth_state"] == query["state"][0]


def test_existing_installation_connects_after_user_oauth(
    callback_app,
    monkeypatch,
):
    from fastapi.testclient import TestClient

    app, database = callback_app
    installation_id = 987654
    installation = github_client.GitHubInstallation(
        id=installation_id,
        app_id=int(get_settings().github_app_id),
        account_login="octo-org",
    )
    fake_token = _stub_oauth(monkeypatch, [installation])
    persisted, connection_id = _stub_persistence(monkeypatch, installation_id)

    client = TestClient(app, follow_redirects=False)
    state = _query(client.get("/api/github/connect-url").json()["url"])["state"][0]
    response = client.get(
        "/api/github/oauth/callback",
        params={"code": "existing-installation-code", "state": state},
    )

    assert response.headers["location"] == (
        f"{get_settings().frontend_base_url}/projects"
    )
    assert persisted == {
        "database": database,
        "github_user_id": 12345,
        "github_login": "octocat",
        "github_installation_id": installation_id,
        "account_login": "octo-org",
    }
    assert database.commits == 1
    session = _session_payload(client)
    assert session == {"github_connection_id": str(connection_id)}
    assert fake_token not in repr(persisted)
    assert fake_token not in json.dumps(session)


def test_no_installation_redirects_to_installation_flow(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    fake_token = _stub_oauth(monkeypatch, [])
    client = TestClient(app, follow_redirects=False)
    oauth_state = _query(
        client.get("/api/github/connect-url").json()["url"]
    )["state"][0]

    response = client.get(
        "/api/github/oauth/callback",
        params={"code": "no-installation-code", "state": oauth_state},
    )

    install_url = response.headers["location"]
    assert install_url.startswith(
        "https://github.com/apps/buglens-test-app/installations/new?"
    )
    install_state = _query(install_url)["state"][0]
    assert install_state != oauth_state
    session = _session_payload(client)
    assert session == {"github_oauth_state": install_state}
    assert fake_token not in json.dumps(session)


def test_first_installation_callback_is_verified_and_connected(
    callback_app,
    monkeypatch,
):
    from fastapi.testclient import TestClient

    app, database = callback_app
    installation_id = 876543
    installation_results = [
        [],
        [
            github_client.GitHubInstallation(
                id=installation_id,
                app_id=int(get_settings().github_app_id),
                account_login="new-org",
            )
        ],
    ]

    def next_installations():
        return installation_results.pop(0)

    _stub_oauth(monkeypatch, next_installations)
    persisted, connection_id = _stub_persistence(monkeypatch, installation_id)
    client = TestClient(app, follow_redirects=False)

    oauth_state = _query(
        client.get("/api/github/connect-url").json()["url"]
    )["state"][0]
    install_response = client.get(
        "/api/github/oauth/callback",
        params={"code": "initial-oauth-code", "state": oauth_state},
    )
    install_state = _query(install_response.headers["location"])["state"][0]

    callback_response = client.get(
        "/api/github/oauth/callback",
        params={
            "code": "installation-oauth-code",
            "state": install_state,
            "installation_id": installation_id,
            "setup_action": "install",
        },
    )

    assert callback_response.headers["location"] == (
        f"{get_settings().frontend_base_url}/projects"
    )
    assert persisted["github_installation_id"] == installation_id
    assert database.commits == 1
    assert _session_payload(client) == {
        "github_connection_id": str(connection_id)
    }
    assert installation_results == []


def test_oauth_state_is_single_use_and_replay_is_rejected():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app, follow_redirects=False)
    state = _query(client.get("/api/github/connect-url").json()["url"])["state"][0]

    missing_code = client.get(
        "/api/github/oauth/callback",
        params={"state": state},
    )
    replay = client.get(
        "/api/github/oauth/callback",
        params={"code": "replayed-code", "state": state},
    )

    assert missing_code.headers["location"].endswith("github_error=missing_code")
    assert replay.headers["location"].endswith("github_error=invalid_state")


def test_installation_hint_for_another_github_app_is_rejected(
    monkeypatch,
):
    from fastapi.testclient import TestClient

    from app.main import app

    installation_id = 765432
    _stub_oauth(
        monkeypatch,
        [
            github_client.GitHubInstallation(
                id=installation_id,
                app_id=int(get_settings().github_app_id) + 1,
                account_login="other-app-org",
            )
        ],
    )
    client = TestClient(app, follow_redirects=False)
    state = _query(client.get("/api/github/connect-url").json()["url"])["state"][0]

    response = client.get(
        "/api/github/oauth/callback",
        params={
            "code": "wrong-app-code",
            "state": state,
            "installation_id": installation_id,
            "setup_action": "install",
        },
    )

    assert response.headers["location"].endswith("github_error=app_not_installed")


@pytest.mark.parametrize("code", ["", None])
def test_oauth_callback_rejects_missing_or_empty_code(code):
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app, follow_redirects=False)
    state = _query(client.get("/api/github/connect-url").json()["url"])["state"][0]
    params = {"state": state}
    if code is not None:
        params["code"] = code

    response = client.get("/api/github/oauth/callback", params=params)

    assert response.headers["location"].endswith("github_error=missing_code")


def test_oauth_callback_handles_rejected_or_malformed_code(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    async def reject_code(**kwargs):
        raise github_client.GitHubOAuthError("bad verification code")

    monkeypatch.setattr(github_client, "exchange_code_for_token", reject_code)
    client = TestClient(app, follow_redirects=False)
    state = _query(client.get("/api/github/connect-url").json()["url"])["state"][0]

    response = client.get(
        "/api/github/oauth/callback",
        params={"code": "malformed-code", "state": state},
    )

    assert response.headers["location"].endswith("github_error=oauth_failed")
