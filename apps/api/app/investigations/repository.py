"""Installation-scoped persistence for bug investigations."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models

if TYPE_CHECKING:
    from .analyzer import BugAnalysis


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


@dataclass(frozen=True)
class PersistedAnalysis:
    id: uuid.UUID
    investigation_id: uuid.UUID
    model_name: str
    summary: str
    observed_behavior: str
    expected_behavior: str | None
    reproduction_steps: list[str]
    error_signals: list[str]
    suspected_components: list[str]
    confidence: str
    needs_more_information: bool
    missing_information: list[str]
    created_at: datetime


class AnalysisClaimState(StrEnum):
    READY = "ready"
    NOT_FOUND = "not_found"
    NO_EVIDENCE = "no_evidence"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class AnalysisClaim:
    state: AnalysisClaimState
    investigation: PersistedInvestigation | None = None
    evidence: list[PersistedEvidence] | None = None


@dataclass(frozen=True)
class AnalysisSnapshot:
    status: str
    analysis: PersistedAnalysis | None


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


async def claim_analysis(
    db: AsyncSession,
    *,
    installation_id: uuid.UUID,
    investigation_id: uuid.UUID,
) -> AnalysisClaim:
    row = (
        await db.execute(
            select(models.Investigation, models.Project)
            .join(models.Project, models.Investigation.project_id == models.Project.id)
            .where(
                models.Investigation.id == investigation_id,
                models.Project.github_installation_id == installation_id,
            )
            .with_for_update(of=models.Investigation)
        )
    ).first()
    if row is None:
        return AnalysisClaim(state=AnalysisClaimState.NOT_FOUND)

    investigation, project = row
    if investigation.status not in (
        models.InvestigationStatus.PENDING.value,
        models.InvestigationStatus.FAILED.value,
    ):
        return AnalysisClaim(state=AnalysisClaimState.CONFLICT)

    result = await db.execute(
        select(models.InvestigationEvidence)
        .where(models.InvestigationEvidence.investigation_id == investigation_id)
        .order_by(
            models.InvestigationEvidence.created_at,
            models.InvestigationEvidence.id,
        )
    )
    evidence = [_to_persisted_evidence(item) for item in result.scalars()]
    if not evidence:
        return AnalysisClaim(state=AnalysisClaimState.NO_EVIDENCE)

    investigation.status = models.InvestigationStatus.RUNNING.value
    await db.flush()
    return AnalysisClaim(
        state=AnalysisClaimState.READY,
        investigation=_to_persisted_investigation(investigation, project),
        evidence=evidence,
    )


async def complete_analysis(
    db: AsyncSession,
    *,
    investigation_id: uuid.UUID,
    model_name: str,
    analysis: "BugAnalysis",
) -> PersistedAnalysis:
    values = {
        "investigation_id": investigation_id,
        "model_name": model_name,
        "summary": analysis.summary,
        "observed_behavior": analysis.observed_behavior,
        "expected_behavior": analysis.expected_behavior,
        "reproduction_steps": analysis.reproduction_steps,
        "error_signals": analysis.error_signals,
        "suspected_components": analysis.suspected_components,
        "confidence": analysis.confidence,
        "needs_more_information": analysis.needs_more_information,
        "missing_information": analysis.missing_information,
    }
    statement = (
        insert(models.InvestigationAnalysis)
        .values(**values)
        .on_conflict_do_update(
            constraint="uq_investigation_analyses_investigation_id",
            set_={
                **{key: value for key, value in values.items() if key != "investigation_id"},
                "updated_at": func.now(),
            },
        )
        .returning(models.InvestigationAnalysis)
    )
    persisted = (await db.execute(statement)).scalar_one()
    await db.execute(
        update(models.Investigation)
        .where(
            models.Investigation.id == investigation_id,
            models.Investigation.status == models.InvestigationStatus.RUNNING.value,
        )
        .values(status=models.InvestigationStatus.COMPLETED.value)
    )
    return _to_persisted_analysis(persisted)


async def mark_analysis_failed(
    db: AsyncSession, *, investigation_id: uuid.UUID
) -> None:
    await db.execute(
        update(models.Investigation)
        .where(
            models.Investigation.id == investigation_id,
            models.Investigation.status == models.InvestigationStatus.RUNNING.value,
        )
        .values(status=models.InvestigationStatus.FAILED.value)
    )


async def get_analysis(
    db: AsyncSession,
    *,
    installation_id: uuid.UUID,
    investigation_id: uuid.UUID,
) -> AnalysisSnapshot | None:
    row = (
        await db.execute(
            select(models.Investigation, models.InvestigationAnalysis)
            .join(models.Project, models.Investigation.project_id == models.Project.id)
            .outerjoin(
                models.InvestigationAnalysis,
                models.InvestigationAnalysis.investigation_id
                == models.Investigation.id,
            )
            .where(
                models.Investigation.id == investigation_id,
                models.Project.github_installation_id == installation_id,
            )
        )
    ).first()
    if row is None:
        return None
    investigation, analysis = row
    return AnalysisSnapshot(
        status=investigation.status,
        analysis=_to_persisted_analysis(analysis) if analysis is not None else None,
    )


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


def _to_persisted_analysis(
    analysis: models.InvestigationAnalysis,
) -> PersistedAnalysis:
    return PersistedAnalysis(
        id=analysis.id,
        investigation_id=analysis.investigation_id,
        model_name=analysis.model_name,
        summary=analysis.summary,
        observed_behavior=analysis.observed_behavior,
        expected_behavior=analysis.expected_behavior,
        reproduction_steps=analysis.reproduction_steps,
        error_signals=analysis.error_signals,
        suspected_components=analysis.suspected_components,
        confidence=analysis.confidence,
        needs_more_information=analysis.needs_more_information,
        missing_information=analysis.missing_information,
        created_at=analysis.created_at,
    )
