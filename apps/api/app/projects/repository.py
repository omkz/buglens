"""Persistence operations for installation-owned Buglensa projects."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models

PROJECT_REPOSITORY_UNIQUE_CONSTRAINT = "uq_projects_installation_repository"


class DuplicateProjectError(Exception):
    """Raised when an installation already has this GitHub repository."""


@dataclass(frozen=True)
class PersistedProject:
    id: uuid.UUID
    name: str
    github_repository_id: int
    github_repository_name: str
    github_repository_full_name: str
    default_branch: str
    app_url: str | None
    created_at: datetime


async def create_project(
    db: AsyncSession,
    *,
    installation_id: uuid.UUID,
    name: str,
    github_repository_id: int,
    github_repository_name: str,
    github_repository_full_name: str,
    default_branch: str,
    app_url: str | None,
) -> PersistedProject:
    """Atomically create a project or report the installation-local conflict."""
    stmt = (
        pg_insert(models.Project)
        .values(
            github_installation_id=installation_id,
            name=name,
            github_repository_id=github_repository_id,
            github_repository_name=github_repository_name,
            github_repository_full_name=github_repository_full_name,
            default_branch=default_branch,
            app_url=app_url,
        )
        .on_conflict_do_nothing(constraint=PROJECT_REPOSITORY_UNIQUE_CONSTRAINT)
        .returning(models.Project)
    )
    project = (await db.execute(stmt)).scalar_one_or_none()
    if project is None:
        raise DuplicateProjectError
    return _to_persisted_project(project)


async def list_projects(
    db: AsyncSession, *, installation_id: uuid.UUID
) -> list[PersistedProject]:
    result = await db.execute(
        select(models.Project)
        .where(models.Project.github_installation_id == installation_id)
        .order_by(models.Project.created_at.desc(), models.Project.id)
    )
    return [_to_persisted_project(project) for project in result.scalars()]


_UNSET = object()


async def update_project(
    db: AsyncSession,
    *,
    installation_id: uuid.UUID,
    project_id: uuid.UUID,
    name: str | object = _UNSET,
    app_url: str | None | object = _UNSET,
) -> PersistedProject | None:
    """Update editable metadata on an installation-owned project."""
    values: dict[str, str | None] = {}
    if name is not _UNSET:
        values["name"] = str(name)
    if app_url is not _UNSET:
        values["app_url"] = None if app_url is None else str(app_url)

    scope = (
        models.Project.id == project_id,
        models.Project.github_installation_id == installation_id,
    )
    if values:
        stmt = (
            update(models.Project)
            .where(*scope)
            .values(**values)
            .returning(models.Project)
        )
    else:
        stmt = select(models.Project).where(*scope)

    project = (await db.execute(stmt)).scalar_one_or_none()
    return _to_persisted_project(project) if project is not None else None


def _to_persisted_project(project: models.Project) -> PersistedProject:
    return PersistedProject(
        id=project.id,
        name=project.name,
        github_repository_id=project.github_repository_id,
        github_repository_name=project.github_repository_name,
        github_repository_full_name=project.github_repository_full_name,
        default_branch=project.default_branch,
        app_url=project.app_url,
        created_at=project.created_at,
    )
