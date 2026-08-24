from __future__ import annotations

import asyncio
import json
import uuid
from base64 import b64decode, b64encode
from unittest.mock import Mock

import httpx
import itsdangerous
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.integrations.github import client as github_client
from app.integrations.github.repository import PersistedGitHubConnection


@pytest.fixture(scope="module")
def rsa_private_key() -> tuple[str, bytes]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem.decode(), public_pem


def _signed_session_cookie(connection_id: str) -> str:
    signer = itsdangerous.TimestampSigner(get_settings().session_secret)
    payload = b64encode(json.dumps({"github_connection_id": connection_id}).encode())
    return signer.sign(payload).decode()


def _decode_session_cookie(cookie: str) -> dict[str, str]:
    signer = itsdangerous.TimestampSigner(get_settings().session_secret)
    return json.loads(b64decode(signer.unsign(cookie)).decode())


def _connection() -> PersistedGitHubConnection:
    return PersistedGitHubConnection(
        connection_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        installation_id=uuid.uuid4(),
        github_installation_id=987654,
        account_login="octo-org",
    )


def _repository_payload(repository_id: int) -> dict[str, object]:
    return {
        "id": repository_id,
        "name": f"repo-{repository_id}",
        "full_name": f"octo-org/repo-{repository_id}",
        "private": repository_id % 2 == 0,
        "default_branch": "main",
        "html_url": f"https://github.com/octo-org/repo-{repository_id}",
    }


def test_app_jwt_uses_rs256_expected_claims_and_normalizes_escaped_newlines(
    monkeypatch, rsa_private_key
):
    private_pem, public_pem = rsa_private_key
    now = 1_800_000_000
    monkeypatch.setattr(github_client.time, "time", lambda: now)

    token = github_client.create_app_jwt(
        client_id="Iv1.test-client-id",
        private_key=private_pem.replace("\n", "\\n"),
    )

    assert jwt.get_unverified_header(token)["alg"] == "RS256"
    claims = jwt.decode(
        token,
        public_pem,
        algorithms=["RS256"],
        options={"verify_exp": False, "verify_iat": False},
    )
    assert claims == {
        "iss": "Iv1.test-client-id",
        "iat": now - 60,
        "exp": now + 600,
    }


def test_installation_access_token_request_uses_app_jwt(monkeypatch):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={"token": "opaque-installation-credential"})

    monkeypatch.setattr(
        github_client,
        "_make_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    token = asyncio.run(
        github_client.create_installation_access_token(
            installation_id=987654,
            app_jwt="signed-app-jwt",
        )
    )

    assert token == "opaque-installation-credential"
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/app/installations/987654/access_tokens"
    assert requests[0].headers["Authorization"] == "Bearer signed-app-jwt"
    assert requests[0].headers["X-GitHub-Api-Version"] == "2022-11-28"


def test_repository_listing_paginates_and_maps_response(monkeypatch):
    requested_pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer installation-credential"
        assert request.url.params["per_page"] == "100"
        page = int(request.url.params["page"])
        requested_pages.append(page)
        start = 1 if page == 1 else 101
        count = 100 if page == 1 else 1
        return httpx.Response(
            200,
            json={
                "repositories": [
                    _repository_payload(repository_id)
                    for repository_id in range(start, start + count)
                ]
            },
        )

    monkeypatch.setattr(
        github_client,
        "_make_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    repositories = asyncio.run(
        github_client.list_installation_repositories("installation-credential")
    )

    assert requested_pages == [1, 2]
    assert len(repositories) == 101
    assert repositories[0] == github_client.GitHubRepository(
        id=1,
        name="repo-1",
        full_name="octo-org/repo-1",
        private=False,
        default_branch="main",
        html_url="https://github.com/octo-org/repo-1",
    )
    assert repositories[-1].id == 101


def test_repositories_without_session_returns_401():
    from fastapi.testclient import TestClient

    from app.main import app

    response = TestClient(app).get("/api/github/repositories")

    assert response.status_code == 401
    assert response.json() == {"detail": "GitHub is not connected."}


def test_repositories_with_malformed_connection_id_returns_401():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    client.cookies.set("buglens_session", _signed_session_cookie("not-a-uuid"))

    response = client.get("/api/github/repositories")

    assert response.status_code == 401
    assert response.json() == {"detail": "GitHub is not connected."}


def test_repositories_with_missing_persisted_connection_returns_401(monkeypatch):
    from fastapi.testclient import TestClient

    from app.integrations.github import routes
    from app.main import app

    async def missing_lookup(*args, **kwargs):
        return None

    monkeypatch.setattr(routes, "get_connection_by_id", missing_lookup)
    client = TestClient(app)
    client.cookies.set("buglens_session", _signed_session_cookie(str(uuid.uuid4())))

    response = client.get("/api/github/repositories")

    assert response.status_code == 401
    assert response.json() == {"detail": "GitHub is not connected."}


def test_repositories_database_failure_returns_503(monkeypatch):
    from fastapi.testclient import TestClient

    from app.integrations.github import routes
    from app.main import app

    async def fail_lookup(*args, **kwargs):
        raise SQLAlchemyError("private database detail")

    test_logger = Mock()
    monkeypatch.setattr(routes, "get_connection_by_id", fail_lookup)
    monkeypatch.setattr(routes, "logger", test_logger)
    client = TestClient(app)
    client.cookies.set("buglens_session", _signed_session_cookie(str(uuid.uuid4())))

    response = client.get("/api/github/repositories")

    assert response.status_code == 503
    assert response.json() == {
        "detail": "GitHub repositories are temporarily unavailable."
    }
    assert "private database detail" not in response.text
    test_logger.exception.assert_called_once_with("github_repositories_db_failed")


def test_repositories_github_failure_returns_safe_502(monkeypatch):
    from fastapi.testclient import TestClient

    from app.integrations.github import routes
    from app.main import app

    connection = _connection()

    async def lookup(*args, **kwargs):
        return connection

    monkeypatch.setattr(routes, "get_connection_by_id", lookup)
    monkeypatch.setattr(
        github_client,
        "create_app_jwt",
        Mock(side_effect=github_client.GitHubAPIError("private auth detail")),
    )
    test_logger = Mock()
    monkeypatch.setattr(routes, "logger", test_logger)
    client = TestClient(app)
    client.cookies.set("buglens_session", _signed_session_cookie(str(uuid.uuid4())))

    response = client.get("/api/github/repositories")

    assert response.status_code == 502
    assert response.json() == {"detail": "Unable to load GitHub repositories."}
    assert "private auth detail" not in response.text
    test_logger.exception.assert_called_once_with(
        "github_repositories_api_failed",
        installation_id=connection.github_installation_id,
    )


def test_repositories_use_persisted_installation_and_keep_credentials_ephemeral(
    monkeypatch,
):
    from fastapi.testclient import TestClient

    from app.integrations.github import routes
    from app.main import app

    connection = _connection()
    private_key = "private-key-must-stay-server-side"
    app_jwt = "app-jwt-must-stay-in-memory"
    installation_token = "installation-token-must-stay-in-memory"

    async def lookup(*args, **kwargs):
        return connection

    def create_jwt(*, client_id, private_key: str):
        assert client_id == "test-client-id"
        assert private_key == "private-key-must-stay-server-side"
        return app_jwt

    async def create_token(*, installation_id, app_jwt: str):
        assert installation_id == connection.github_installation_id
        assert app_jwt == "app-jwt-must-stay-in-memory"
        return installation_token

    async def list_repositories(token: str):
        assert token == installation_token
        return [github_client.GitHubRepository(**_repository_payload(123))]

    monkeypatch.setattr(routes, "get_connection_by_id", lookup)
    monkeypatch.setattr(github_client, "create_app_jwt", create_jwt)
    monkeypatch.setattr(github_client, "create_installation_access_token", create_token)
    monkeypatch.setattr(
        github_client, "list_installation_repositories", list_repositories
    )
    test_logger = Mock()
    monkeypatch.setattr(routes, "logger", test_logger)

    settings = get_settings().model_copy(
        update={"github_private_key": private_key}
    )
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        client = TestClient(app)
        client.cookies.set(
            "buglens_session", _signed_session_cookie(str(connection.connection_id))
        )
        response = client.get("/api/github/repositories?installation_id=123")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"repositories": [_repository_payload(123)]}
    session_payload = _decode_session_cookie(client.cookies["buglens_session"])
    assert session_payload == {"github_connection_id": str(connection.connection_id)}
    assert app_jwt not in response.text
    assert installation_token not in response.text
    assert private_key not in response.text
    test_logger.info.assert_called_once_with(
        "github_repositories_loaded",
        installation_id=connection.github_installation_id,
        repository_count=1,
    )
