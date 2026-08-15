"""Installation-scoped persistence for bug investigations."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models


@dataclass(frozen=True)
class PersistedInvestigation:
    id: uuid.UUID
    project_id: uuid.UUID
    project_name: str
    github_repository_full_name: str
    title: str
    description: str | None
    status: str
    created_at: datetime


async def create_investigation(
    db: AsyncSession,
    *,
    installation_id: uuid.UUID,
    project_id: uuid.UUID,
    title: str,
    description: str | None,
) -> PersistedInvestigation | None:
    project = (
        await db.execute(
            select(models.Project).where(
                models.Project.id == project_id,
                models.Project.github_installation_id == installation_id,
            )
        )
    ).scalar_one_or_none()
    if project is None:
        return None

    investigation = models.Investigation(
        project_id=project.id,
        title=title,
        description=description,
    )
    db.add(investigation)
    await db.flush()
    return _to_persisted_investigation(investigation, project)


async def list_investigations(
    db: AsyncSession, *, installation_id: uuid.UUID
) -> list[PersistedInvestigation]:
    result = await db.execute(
        select(models.Investigation, models.Project)
        .join(models.Project, models.Investigation.project_id == models.Project.id)
        .where(models.Project.github_installation_id == installation_id)
        .order_by(models.Investigation.created_at.desc(), models.Investigation.id)
    )
    return [
        _to_persisted_investigation(investigation, project)
        for investigation, project in result.all()
    ]


async def get_investigation(
    db: AsyncSession,
    *,
    installation_id: uuid.UUID,
    investigation_id: uuid.UUID,
) -> PersistedInvestigation | None:
    row = (
        await db.execute(
            select(models.Investigation, models.Project)
            .join(models.Project, models.Investigation.project_id == models.Project.id)
            .where(
                models.Investigation.id == investigation_id,
                models.Project.github_installation_id == installation_id,
            )
        )
    ).first()
    if row is None:
        return None
    investigation, project = row
    return _to_persisted_investigation(investigation, project)


def _to_persisted_investigation(
    investigation: models.Investigation, project: models.Project
) -> PersistedInvestigation:
    return PersistedInvestigation(
        id=investigation.id,
        project_id=project.id,
        project_name=project.name,
        github_repository_full_name=project.github_repository_full_name,
        title=investigation.title,
        description=investigation.description,
        status=investigation.status,
        created_at=investigation.created_at,
    )
