"""Persistence for GitHub connections.

Kept out of the route handler (see routes.py) so the upsert/idempotency
rules can be tested directly against a database without going through
HTTP or GitHub's API. Nothing here knows about sessions, cookies, or
FastAPI -- it only takes plain values in and returns plain values out.

Uses PostgreSQL's native "INSERT ... ON CONFLICT" upserts rather than a
select-then-insert pattern, so reconnecting the same account is atomic and
race-safe under concurrent requests, not just idempotent in the common
case.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models


@dataclass
class PersistedGitHubConnection:
    connection_id: uuid.UUID
    user_id: uuid.UUID
    installation_id: uuid.UUID
    github_installation_id: int
    account_login: str


async def persist_github_connection(
    db: AsyncSession,
    *,
    github_user_id: int,
    github_login: str,
    github_installation_id: int,
    account_login: str,
) -> PersistedGitHubConnection:
    """Upsert the User and GitHubInstallation, then ensure a GitHubConnection
    links them. Does not commit -- the caller owns the transaction boundary.
    """
    user = await _upsert_user(
        db, github_user_id=github_user_id, github_login=github_login
    )
    installation = await _upsert_installation(
        db,
        github_installation_id=github_installation_id,
        account_login=account_login,
    )
    connection = await _ensure_connection(
        db, user_id=user.id, installation_id=installation.id
    )

    return PersistedGitHubConnection(
        connection_id=connection.id,
        user_id=user.id,
        installation_id=installation.id,
        github_installation_id=installation.github_installation_id,
        account_login=installation.account_login,
    )


async def get_connection_by_id(
    db: AsyncSession, *, connection_id: uuid.UUID
) -> PersistedGitHubConnection | None:
    """Look up a connection strictly by its own id (from the browser
    session) -- never by "the most recent connection" or similar, so one
    browser's session can never resolve to another user's connection.
    """
    stmt = (
        select(models.GitHubConnection, models.GitHubInstallation)
        .join(
            models.GitHubInstallation,
            models.GitHubConnection.github_installation_id
            == models.GitHubInstallation.id,
        )
        .where(models.GitHubConnection.id == connection_id)
    )
    result = await db.execute(stmt)
    row = result.first()
    if row is None:
        return None

    connection, installation = row
    return PersistedGitHubConnection(
        connection_id=connection.id,
        user_id=connection.user_id,
        installation_id=installation.id,
        github_installation_id=installation.github_installation_id,
        account_login=installation.account_login,
    )


async def _upsert_user(
    db: AsyncSession, *, github_user_id: int, github_login: str
) -> models.User:
    stmt = (
        pg_insert(models.User)
        .values(github_user_id=github_user_id, github_login=github_login)
        .on_conflict_do_update(
            index_elements=[models.User.github_user_id],
            set_={"github_login": github_login, "updated_at": func.now()},
        )
        .returning(models.User)
    )
    result = await db.execute(stmt)
    return result.scalar_one()


async def _upsert_installation(
    db: AsyncSession, *, github_installation_id: int, account_login: str
) -> models.GitHubInstallation:
    stmt = (
        pg_insert(models.GitHubInstallation)
        .values(
            github_installation_id=github_installation_id,
            account_login=account_login,
        )
        .on_conflict_do_update(
            index_elements=[models.GitHubInstallation.github_installation_id],
            set_={"account_login": account_login, "updated_at": func.now()},
        )
        .returning(models.GitHubInstallation)
    )
    result = await db.execute(stmt)
    return result.scalar_one()


async def _ensure_connection(
    db: AsyncSession, *, user_id: uuid.UUID, installation_id: uuid.UUID
) -> models.GitHubConnection:
    stmt = (
        pg_insert(models.GitHubConnection)
        .values(user_id=user_id, github_installation_id=installation_id)
        .on_conflict_do_nothing(
            index_elements=[
                models.GitHubConnection.user_id,
                models.GitHubConnection.github_installation_id,
            ]
        )
        .returning(models.GitHubConnection)
    )
    result = await db.execute(stmt)
    connection = result.scalar_one_or_none()
    if connection is not None:
        return connection

    # ON CONFLICT DO NOTHING skipped the insert because this pair already
    # exists -- fetch the existing row instead of creating a duplicate.
    existing = await db.execute(
        select(models.GitHubConnection).where(
            models.GitHubConnection.user_id == user_id,
            models.GitHubConnection.github_installation_id == installation_id,
        )
    )
    return existing.scalar_one()
