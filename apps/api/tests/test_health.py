from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app import main


class FakeConnection:
    def __init__(self):
        self.statements = []

    async def execute(self, statement):
        self.statements.append(str(statement))


class ConnectionContext:
    def __init__(self, connection=None, error=None):
        self.connection = connection
        self.error = error

    async def __aenter__(self):
        if self.error is not None:
            raise self.error
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeEngine:
    def __init__(self, *, connection=None, error=None):
        self.connection = connection
        self.error = error
        self.connect_calls = 0
        self.disposed = False

    def connect(self):
        self.connect_calls += 1
        return ConnectionContext(self.connection, self.error)

    async def dispose(self):
        self.disposed = True


def test_health_is_cheap_and_does_not_access_database(monkeypatch):
    class DatabaseMustNotBeUsed:
        def connect(self):
            raise AssertionError("health must not access the database")

    monkeypatch.setattr(main, "engine", DatabaseMustNotBeUsed())

    response = TestClient(main.app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_product_routes_are_mounted_only_under_api_prefix():
    openapi = main.app.openapi()
    paths = set(openapi["paths"])

    assert "/api/github/status" in paths
    assert "/api/projects" in paths
    assert "/api/investigations" in paths
    assert "/api/investigations/{investigation_id}/agent-run/events" in paths
    assert "/github/status" not in paths
    assert "/projects" not in paths
    assert "/investigations" not in paths
    assert not any(path.startswith("/api/api/") for path in paths)
    assert "servers" not in openapi

    client = TestClient(main.app)
    assert client.get("/github/status").status_code == 404
    assert client.get("/projects").status_code == 404
    assert client.get("/investigations").status_code == 404


def test_readiness_returns_ready_after_select_one(monkeypatch):
    connection = FakeConnection()
    fake_engine = FakeEngine(connection=connection)
    monkeypatch.setattr(main, "engine", fake_engine)

    response = TestClient(main.app).get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert fake_engine.connect_calls == 1
    assert connection.statements == ["SELECT 1"]


def test_readiness_failure_is_safe_and_logs_only_exception_type(monkeypatch):
    private_detail = "postgresql://user:secret@private-host/buglens"
    fake_engine = FakeEngine(error=SQLAlchemyError(private_detail))
    logger = Mock()
    monkeypatch.setattr(main, "engine", fake_engine)
    monkeypatch.setattr(main, "logger", logger)

    response = TestClient(main.app).get("/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert private_detail not in response.text
    logger.warning.assert_called_once_with(
        "buglens_api_readiness_failed",
        exception_type="SQLAlchemyError",
    )


@pytest.mark.anyio
async def test_lifespan_disposes_database_engine(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr(main, "engine", fake_engine)

    async with main.lifespan(main.app):
        assert fake_engine.disposed is False

    assert fake_engine.disposed is True
