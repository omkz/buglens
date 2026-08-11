"""Tests for the persisted GitHub connection flow: session-based OAuth
state, idempotent upserts, and status lookups scoped strictly to the
browser's own session.

Tests that need a real database are marked with `requires_database` and
skip cleanly when one isn't reachable, so this file still contributes
useful coverage in environments without PostgreSQL. None of these tests
make a real call to GitHub's API -- app.integrations.github.client
functions are monkeypatched instead.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.db import models
from app.db.session import SessionLocal, engine
from app.integrations.github import client as github_client
from app.integrations.github.repository import (
    get_connection_by_id,
    persist_github_connection,
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


# --- session-based OAuth state (no database required) -----------------


def test_install_url_stores_oauth_state_in_the_session():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app, follow_redirects=False)
    response = client.get("/github/install-url")

    assert response.status_code == 200
    url = response.json()["url"]
    assert url.startswith("https://github.com/apps/buglens-test-app/installations/new")
    state = url.split("state=")[1]
    assert state
    # The session cookie was set for this state to be validated later.
    assert client.cookies.get("buglens_session") is not None


def test_oauth_callback_rejects_missing_session_state():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app, follow_redirects=False)
    # No prior call to /github/install-url, so there is no pending state.
    response = client.get(
        "/github/oauth/callback", params={"code": "irrelevant", "state": "anything"}
    )

    assert response.status_code in (302, 307)
    assert response.headers["location"].endswith("github_error=invalid_state")


def test_oauth_callback_rejects_tampered_state():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app, follow_redirects=False)
    client.get("/github/install-url")  # establishes a real pending state

    response = client.get(
        "/github/oauth/callback",
        params={"code": "irrelevant", "state": "not-the-real-state"},
    )

    assert response.status_code in (302, 307)
    assert response.headers["location"].endswith("github_error=invalid_state")


def test_status_without_session_returns_disconnected():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    response = client.get("/github/status")

    assert response.status_code == 200
    assert response.json() == {
        "connected": False,
        "installation_id": None,
        "account_login": None,
    }


def test_status_with_malformed_session_connection_id_returns_disconnected():
    from fastapi.testclient import TestClient
    from starlette.middleware.sessions import SessionMiddleware

    from app.main import app

    # Forge a session cookie with a non-UUID connection id, the same way a
    # corrupted/forward-incompatible session could look.
    settings = get_settings()
    import itsdangerous
    import json
    from base64 import b64encode

    signer = itsdangerous.TimestampSigner(settings.session_secret)
    payload = b64encode(json.dumps({"github_connection_id": "not-a-uuid"}).encode())
    cookie_value = signer.sign(payload).decode()

    client = TestClient(app)
    client.cookies.set("buglens_session", cookie_value)
    response = client.get("/github/status")

    assert response.status_code == 200
    assert response.json() == {
        "connected": False,
        "installation_id": None,
        "account_login": None,
    }


# --- schema-level checks (no database required) ------------------------


def test_github_connection_relationship_columns_are_present_on_models():
    assert hasattr(models.GitHubConnection, "user_id")
    assert hasattr(models.GitHubConnection, "github_installation_id")
    assert not hasattr(models.GitHubInstallation, "user_id")


# --- persistence + full HTTP flow (requires a real database) -----------


@requires_database
def test_persist_github_connection_is_idempotent_on_reconnect():
    async def _run() -> None:
        test_github_user_id = 900_100_001
        test_github_installation_id = 900_100_002

        try:
            async with SessionLocal() as db:
                first = await persist_github_connection(
                    db,
                    github_user_id=test_github_user_id,
                    github_login="octocat",
                    github_installation_id=test_github_installation_id,
                    account_login="octo-org",
                )
                await db.commit()

            # Reconnect the same account with a changed username/account
            # login -- must update in place, not create duplicates.
            async with SessionLocal() as db:
                second = await persist_github_connection(
                    db,
                    github_user_id=test_github_user_id,
                    github_login="octocat-renamed",
                    github_installation_id=test_github_installation_id,
                    account_login="octo-org-renamed",
                )
                await db.commit()

            assert first.connection_id == second.connection_id
            assert first.user_id == second.user_id
            assert first.installation_id == second.installation_id

            async with SessionLocal() as db:
                users = (
                    (
                        await db.execute(
                            select(models.User).where(
                                models.User.github_user_id == test_github_user_id
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                installations = (
                    (
                        await db.execute(
                            select(models.GitHubInstallation).where(
                                models.GitHubInstallation.github_installation_id
                                == test_github_installation_id
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                connections = (
                    (
                        await db.execute(
                            select(models.GitHubConnection).where(
                                models.GitHubConnection.user_id == second.user_id
                            )
                        )
                    )
                    .scalars()
                    .all()
                )

            assert len(users) == 1
            assert users[0].github_login == "octocat-renamed"
            assert len(installations) == 1
            assert installations[0].account_login == "octo-org-renamed"
            assert len(connections) == 1

            looked_up = await _lookup(second.connection_id)
            assert looked_up is not None
            assert looked_up.account_login == "octo-org-renamed"
        finally:
            await _cleanup(test_github_user_id, test_github_installation_id)

    asyncio.run(_run())


@requires_database
def test_github_connection_unique_constraint_rejects_direct_duplicate_insert():
    async def _run() -> None:
        test_github_user_id = 900_100_011
        test_github_installation_id = 900_100_012

        try:
            async with SessionLocal() as db:
                connection = await persist_github_connection(
                    db,
                    github_user_id=test_github_user_id,
                    github_login="octocat",
                    github_installation_id=test_github_installation_id,
                    account_login="octo-org",
                )
                await db.commit()

            with pytest.raises(IntegrityError):
                async with SessionLocal() as db:
                    db.add(
                        models.GitHubConnection(
                            user_id=connection.user_id,
                            github_installation_id=connection.installation_id,
                        )
                    )
                    await db.commit()
        finally:
            await _cleanup(test_github_user_id, test_github_installation_id)

    asyncio.run(_run())


@requires_database
def test_oauth_callback_full_flow_persists_connection_without_storing_token(
    monkeypatch,
):
    from fastapi.testclient import TestClient

    from app.main import app

    test_github_user_id = 900_100_021
    test_github_installation_id = 900_100_022
    fake_token = "ghu_fake_token_should_never_be_persisted"

    async def fake_exchange(**kwargs):
        return fake_token

    async def fake_user(access_token):
        assert access_token == fake_token
        return github_client.GitHubUser(id=test_github_user_id, login="octocat")

    async def fake_installations(access_token):
        return [
            github_client.GitHubInstallation(
                id=test_github_installation_id,
                app_id=int(get_settings().github_app_id),
                account_login="octo-org",
            )
        ]

    monkeypatch.setattr(github_client, "exchange_code_for_token", fake_exchange)
    monkeypatch.setattr(github_client, "fetch_authenticated_user", fake_user)
    monkeypatch.setattr(
        github_client, "fetch_user_installations", fake_installations
    )

    try:
        client = TestClient(app, follow_redirects=False)
        install_response = client.get("/github/install-url")
        state = install_response.json()["url"].split("state=")[1]

        callback_response = client.get(
            "/github/oauth/callback",
            params={
                "code": "fake-code",
                "state": state,
                "installation_id": test_github_installation_id,
                "setup_action": "install",
            },
        )
        assert callback_response.headers["location"] == (
            f"{get_settings().frontend_base_url}/projects"
        )

        status = client.get("/github/status").json()
        assert status == {
            "connected": True,
            "installation_id": test_github_installation_id,
            "account_login": "octo-org",
        }

        # A brand new session (no cookie) must not see this connection.
        other_client = TestClient(app)
        assert other_client.get("/github/status").json() == {
            "connected": False,
            "installation_id": None,
            "account_login": None,
        }

        async def assert_token_not_persisted() -> None:
            async with SessionLocal() as db:
                result = await db.execute(select(models.User.__table__))
                for row in result:
                    assert fake_token not in str(row)
                result = await db.execute(select(models.GitHubInstallation.__table__))
                for row in result:
                    assert fake_token not in str(row)
                result = await db.execute(select(models.GitHubConnection.__table__))
                for row in result:
                    assert fake_token not in str(row)

        asyncio.run(assert_token_not_persisted())
    finally:
        asyncio.run(_cleanup(test_github_user_id, test_github_installation_id))


async def _lookup(connection_id: uuid.UUID):
    async with SessionLocal() as db:
        return await get_connection_by_id(db, connection_id=connection_id)


async def _cleanup(github_user_id: int, github_installation_id: int) -> None:
    async with SessionLocal() as db:
        user = (
            await db.execute(
                select(models.User).where(models.User.github_user_id == github_user_id)
            )
        ).scalar_one_or_none()
        if user is not None:
            await db.execute(
                delete(models.GitHubConnection).where(
                    models.GitHubConnection.user_id == user.id
                )
            )
            await db.execute(delete(models.User).where(models.User.id == user.id))
        await db.execute(
            delete(models.GitHubInstallation).where(
                models.GitHubInstallation.github_installation_id
                == github_installation_id
            )
        )
        await db.commit()
