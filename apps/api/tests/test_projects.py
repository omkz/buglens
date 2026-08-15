from __future__ import annotations

import asyncio
import json
import uuid
from base64 import b64encode
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

import httpx
import itsdangerous
import pytest
from sqlalchemy import delete
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.db import models
from app.db.session import SessionLocal, engine
from app.integrations.github import client as github_client
from app.integrations.github.repository import (
    PersistedGitHubConnection,
    persist_github_connection,
)
from app.projects.repository import (
    DuplicateProjectError,
    PersistedProject,
    create_project,
    list_projects,
)


def _database_is_reachable() -> bool:
    async def _check() -> bool:
        try:
            async with engine.connect():
                return True
        except Exception:
            return False

    return asyncio.run(_check())


requires_database = pytest.mark.skipif(
    not _database_is_reachable(), reason="requires a reachable PostgreSQL database"
)


def _signed_session_cookie(connection_id: str) -> str:
    signer = itsdangerous.TimestampSigner(get_settings().session_secret)
    payload = b64encode(json.dumps({"github_connection_id": connection_id}).encode())
    return signer.sign(payload).decode()


def _connection() -> PersistedGitHubConnection:
    return PersistedGitHubConnection(
        connection_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        installation_id=uuid.uuid4(),
        github_installation_id=987654,
        account_login="octo-org",
    )


def _repository(repository_id: int = 123) -> github_client.GitHubRepository:
    return github_client.GitHubRepository(
        id=repository_id,
        name="trusted-name",
        full_name="octo-org/trusted-name",
        private=True,
        default_branch="trusted-default",
        html_url="https://github.com/octo-org/trusted-name",
    )


def _project(repository_id: int = 123) -> PersistedProject:
    return PersistedProject(
        id=uuid.uuid4(),
        name="Checkout Demo",
        github_repository_id=repository_id,
        github_repository_name="trusted-name",
        github_repository_full_name="octo-org/trusted-name",
        default_branch="trusted-default",
        app_url="https://example.com/",
        created_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
    )


def _connected_client(monkeypatch, connection: PersistedGitHubConnection):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.projects import routes

    async def lookup(*args, **kwargs):
        return connection

    monkeypatch.setattr(routes, "get_connection_by_id", lookup)
    client = TestClient(app)
    client.cookies.set(
        "buglens_session", _signed_session_cookie(str(connection.connection_id))
    )
    return client


def test_create_project_requires_session():
    from fastapi.testclient import TestClient

    from app.main import app

    response = TestClient(app).post(
        "/projects", json={"name": "Demo", "github_repository_id": 123}
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "GitHub is not connected."}


def test_create_project_rejects_malformed_connection_id():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    client.cookies.set("buglens_session", _signed_session_cookie("not-a-uuid"))
    response = client.post(
        "/projects", json={"name": "Demo", "github_repository_id": 123}
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "GitHub is not connected."}


def test_create_project_rejects_missing_persisted_connection(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.projects import routes

    async def missing(*args, **kwargs):
        return None

    monkeypatch.setattr(routes, "get_connection_by_id", missing)
    client = TestClient(app)
    client.cookies.set("buglens_session", _signed_session_cookie(str(uuid.uuid4())))
    response = client.post(
        "/projects", json={"name": "Demo", "github_repository_id": 123}
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "GitHub is not connected."}


def test_create_project_does_not_accept_browser_installation_id(monkeypatch):
    client = _connected_client(monkeypatch, _connection())

    response = client.post(
        "/projects",
        json={
            "name": "Demo",
            "github_repository_id": 123,
            "github_installation_id": 999999,
        },
    )

    assert response.status_code == 422


def test_create_project_validates_repository_and_uses_trusted_metadata(monkeypatch):
    from app.projects import routes

    connection = _connection()
    trusted_repository = _repository()
    persisted = _project()
    captured: dict[str, object] = {}

    async def load_repositories(*, settings, github_installation_id):
        captured["validated_installation_id"] = github_installation_id
        return [trusted_repository]

    async def persist(db, **kwargs):
        captured["persisted"] = kwargs
        return persisted

    monkeypatch.setattr(routes, "load_installation_repositories", load_repositories)
    monkeypatch.setattr(routes, "persist_project", persist)
    client = _connected_client(monkeypatch, connection)

    response = client.post(
        "/projects",
        json={
            "name": "Checkout Demo",
            "github_repository_id": 123,
            "app_url": "https://example.com",
        },
    )

    assert response.status_code == 201
    assert response.json() == {
        "id": str(persisted.id),
        "name": "Checkout Demo",
        "github_repository_id": 123,
        "github_repository_full_name": "octo-org/trusted-name",
        "default_branch": "trusted-default",
        "app_url": "https://example.com/",
        "created_at": "2026-08-16T00:00:00Z",
    }
    assert captured["validated_installation_id"] == connection.github_installation_id
    assert captured["persisted"] == {
        "installation_id": connection.installation_id,
        "name": "Checkout Demo",
        "github_repository_id": 123,
        "github_repository_name": "trusted-name",
        "github_repository_full_name": "octo-org/trusted-name",
        "default_branch": "trusted-default",
        "app_url": "https://example.com/",
    }


def test_create_project_rejects_inaccessible_repository(monkeypatch):
    from app.projects import routes

    persist = AsyncMock()

    async def load_repositories(**kwargs):
        return [_repository(456)]

    monkeypatch.setattr(routes, "load_installation_repositories", load_repositories)
    monkeypatch.setattr(routes, "persist_project", persist)
    client = _connected_client(monkeypatch, _connection())

    response = client.post(
        "/projects", json={"name": "Demo", "github_repository_id": 123}
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "The selected GitHub repository is not accessible."
    }
    persist.assert_not_awaited()


def test_duplicate_repository_returns_conflict(monkeypatch):
    from app.projects import routes

    async def load_repositories(**kwargs):
        return [_repository()]

    async def duplicate(*args, **kwargs):
        raise DuplicateProjectError

    monkeypatch.setattr(routes, "load_installation_repositories", load_repositories)
    monkeypatch.setattr(routes, "persist_project", duplicate)
    client = _connected_client(monkeypatch, _connection())

    response = client.post(
        "/projects", json={"name": "Demo", "github_repository_id": 123}
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "A project already exists for this GitHub repository."
    }


def test_project_connection_database_failure_returns_safe_503(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.projects import routes

    async def fail(*args, **kwargs):
        raise SQLAlchemyError("private database detail")

    test_logger = Mock()
    monkeypatch.setattr(routes, "get_connection_by_id", fail)
    monkeypatch.setattr(routes, "logger", test_logger)
    client = TestClient(app)
    client.cookies.set("buglens_session", _signed_session_cookie(str(uuid.uuid4())))

    response = client.get("/projects")

    assert response.status_code == 503
    assert response.json() == {"detail": "Projects are temporarily unavailable."}
    assert "private database detail" not in response.text
    test_logger.exception.assert_called_once_with("projects_connection_db_failed")


def test_project_persistence_database_failure_returns_safe_503(monkeypatch):
    from app.projects import routes

    async def load_repositories(**kwargs):
        return [_repository()]

    async def fail(*args, **kwargs):
        raise SQLAlchemyError("private database detail")

    test_logger = Mock()
    monkeypatch.setattr(routes, "load_installation_repositories", load_repositories)
    monkeypatch.setattr(routes, "persist_project", fail)
    monkeypatch.setattr(routes, "logger", test_logger)
    connection = _connection()
    client = _connected_client(monkeypatch, connection)

    response = client.post(
        "/projects", json={"name": "Demo", "github_repository_id": 123}
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Projects are temporarily unavailable."}
    assert "private database detail" not in response.text
    test_logger.exception.assert_called_once_with(
        "project_persist_failed",
        installation_id=connection.github_installation_id,
        repository_id=123,
    )


def test_github_validation_failure_returns_safe_502(monkeypatch):
    from app.projects import routes

    async def fail(**kwargs):
        raise httpx.HTTPError("private token detail")

    test_logger = Mock()
    monkeypatch.setattr(routes, "load_installation_repositories", fail)
    monkeypatch.setattr(routes, "logger", test_logger)
    connection = _connection()
    client = _connected_client(monkeypatch, connection)

    response = client.post(
        "/projects", json={"name": "Demo", "github_repository_id": 123}
    )

    assert response.status_code == 502
    assert response.json() == {
        "detail": "Unable to validate the GitHub repository."
    }
    assert "private token detail" not in response.text
    test_logger.exception.assert_called_once_with(
        "project_repository_validation_failed",
        installation_id=connection.github_installation_id,
        repository_id=123,
    )


def test_get_projects_scopes_to_current_installation(monkeypatch):
    from app.projects import routes

    connection = _connection()
    current_project = _project()
    unrelated_project = _project(999)
    captured: dict[str, object] = {}

    async def scoped_list(db, *, installation_id):
        captured["installation_id"] = installation_id
        # The repository layer applies the installation filter; an unrelated
        # installation's project is deliberately not returned.
        assert unrelated_project.github_repository_id == 999
        return [current_project]

    monkeypatch.setattr(routes, "load_projects", scoped_list)
    client = _connected_client(monkeypatch, connection)

    response = client.get("/projects?installation_id=999999")

    assert response.status_code == 200
    assert captured["installation_id"] == connection.installation_id
    assert [project["github_repository_id"] for project in response.json()["projects"]] == [123]


def test_project_models_never_store_github_credentials():
    from app.db.base import Base

    columns = set(Base.metadata.tables["projects"].columns.keys())
    assert columns.isdisjoint(
        {
            "github_private_key",
            "github_app_jwt",
            "github_installation_token",
            "access_token",
        }
    )


@requires_database
def test_project_repository_enforces_uniqueness_and_installation_scope():
    async def _run() -> None:
        first_user_id = 900_200_001
        first_installation_id = 900_200_002
        second_user_id = 900_200_003
        second_installation_id = 900_200_004

        try:
            async with SessionLocal() as db:
                first_connection = await persist_github_connection(
                    db,
                    github_user_id=first_user_id,
                    github_login="first-user",
                    github_installation_id=first_installation_id,
                    account_login="first-org",
                )
                second_connection = await persist_github_connection(
                    db,
                    github_user_id=second_user_id,
                    github_login="second-user",
                    github_installation_id=second_installation_id,
                    account_login="second-org",
                )
                await db.commit()

                first_project = await create_project(
                    db,
                    installation_id=first_connection.installation_id,
                    name="First Project",
                    github_repository_id=900_200_005,
                    github_repository_name="shared-repo",
                    github_repository_full_name="first-org/shared-repo",
                    default_branch="main",
                    app_url=None,
                )
                await db.commit()

                with pytest.raises(DuplicateProjectError):
                    await create_project(
                        db,
                        installation_id=first_connection.installation_id,
                        name="Duplicate",
                        github_repository_id=900_200_005,
                        github_repository_name="shared-repo",
                        github_repository_full_name="first-org/shared-repo",
                        default_branch="main",
                        app_url=None,
                    )
                await db.rollback()

                await create_project(
                    db,
                    installation_id=second_connection.installation_id,
                    name="Other Installation",
                    github_repository_id=900_200_005,
                    github_repository_name="shared-repo",
                    github_repository_full_name="second-org/shared-repo",
                    default_branch="main",
                    app_url=None,
                )
                await db.commit()

                visible = await list_projects(
                    db, installation_id=first_connection.installation_id
                )
                assert [project.id for project in visible] == [first_project.id]
                assert all(
                    project.github_repository_full_name.startswith("first-org/")
                    for project in visible
                )
        finally:
            async with SessionLocal() as db:
                await db.execute(
                    delete(models.User).where(
                        models.User.github_user_id.in_([first_user_id, second_user_id])
                    )
                )
                await db.execute(
                    delete(models.GitHubInstallation).where(
                        models.GitHubInstallation.github_installation_id.in_(
                            [first_installation_id, second_installation_id]
                        )
                    )
                )
                await db.commit()

    asyncio.run(_run())
