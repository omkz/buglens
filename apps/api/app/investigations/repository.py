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


@dataclass(frozen=True)
class EvidenceDraft:
    id: uuid.UUID
    kind: str
    mime_type: str | None
    filename: str | None
    storage_key: str | None
    size_bytes: int | None
    text_content: str | None


@dataclass(frozen=True)
class PersistedEvidence:
    id: uuid.UUID
    investigation_id: uuid.UUID
    kind: str
    mime_type: str | None
    filename: str | None
    storage_key: str | None
    size_bytes: int | None
    text_content: str | None
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


async def create_evidence_items(
    db: AsyncSession,
    *,
    installation_id: uuid.UUID,
    investigation_id: uuid.UUID,
    items: list[EvidenceDraft],
) -> list[PersistedEvidence] | None:
    if not await _investigation_is_accessible(
        db,
        installation_id=installation_id,
        investigation_id=investigation_id,
    ):
        return None

    evidence_items = [
        models.InvestigationEvidence(
            id=item.id,
            investigation_id=investigation_id,
            kind=item.kind,
            mime_type=item.mime_type,
            filename=item.filename,
            storage_key=item.storage_key,
            size_bytes=item.size_bytes,
            text_content=item.text_content,
        )
        for item in items
    ]
    db.add_all(evidence_items)
    await db.flush()
    return [_to_persisted_evidence(item) for item in evidence_items]


async def list_evidence_items(
    db: AsyncSession,
    *,
    installation_id: uuid.UUID,
    investigation_id: uuid.UUID,
) -> list[PersistedEvidence] | None:
    if not await _investigation_is_accessible(
        db,
        installation_id=installation_id,
        investigation_id=investigation_id,
    ):
        return None

    result = await db.execute(
        select(models.InvestigationEvidence)
        .where(models.InvestigationEvidence.investigation_id == investigation_id)
        .order_by(
            models.InvestigationEvidence.created_at,
            models.InvestigationEvidence.id,
        )
    )
    return [_to_persisted_evidence(item) for item in result.scalars()]


async def get_recording_evidence(
    db: AsyncSession,
    *,
    installation_id: uuid.UUID,
    investigation_id: uuid.UUID,
    evidence_id: uuid.UUID,
) -> PersistedEvidence | None:
    item = (
        await db.execute(
            select(models.InvestigationEvidence)
            .join(
                models.Investigation,
                models.InvestigationEvidence.investigation_id
                == models.Investigation.id,
            )
            .join(models.Project, models.Investigation.project_id == models.Project.id)
            .where(
                models.InvestigationEvidence.id == evidence_id,
                models.InvestigationEvidence.investigation_id == investigation_id,
                models.InvestigationEvidence.kind
                == models.EvidenceKind.RECORDING.value,
                models.Project.github_installation_id == installation_id,
            )
        )
    ).scalar_one_or_none()
    return _to_persisted_evidence(item) if item is not None else None


async def _investigation_is_accessible(
    db: AsyncSession,
    *,
    installation_id: uuid.UUID,
    investigation_id: uuid.UUID,
) -> bool:
    result = await db.execute(
        select(models.Investigation.id)
        .join(models.Project, models.Investigation.project_id == models.Project.id)
        .where(
            models.Investigation.id == investigation_id,
            models.Project.github_installation_id == installation_id,
        )
    )
    return result.scalar_one_or_none() is not None


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


def _to_persisted_evidence(
    evidence: models.InvestigationEvidence,
) -> PersistedEvidence:
    return PersistedEvidence(
        id=evidence.id,
        investigation_id=evidence.investigation_id,
        kind=evidence.kind,
        mime_type=evidence.mime_type,
        filename=evidence.filename,
        storage_key=evidence.storage_key,
        size_bytes=evidence.size_bytes,
        text_content=evidence.text_content,
        created_at=evidence.created_at,
    )
