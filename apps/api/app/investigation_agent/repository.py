"""Row-locked, installation-scoped persistence for autonomous agent runs."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models

from .schemas import AgentInvestigationResult, BrowserExecutionResult

if TYPE_CHECKING:
    from app.investigations.analyzer import BugAnalysis


class AgentRunClaimState(StrEnum):
    READY = "ready"
    NOT_FOUND = "not_found"
    NO_ANALYSIS = "no_analysis"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class AgentRunContext:
    investigation_id: uuid.UUID
    github_installation_id: int
    repository_full_name: str
    default_branch: str
    app_url: str | None
    analysis: "BugAnalysis"


@dataclass(frozen=True)
class AgentRunClaim:
    state: AgentRunClaimState
    context: AgentRunContext | None = None


@dataclass(frozen=True)
class PersistedAgentRun:
    id: uuid.UUID
    investigation_id: uuid.UUID
    status: str
    agent_model: str
    repository_summary: list[dict] | None
    duplicate_candidates: list[dict]
    reproduction_plan: dict | None
    generated_test: str | None
    reproduction_status: str | None
    execution_result: dict | None
    execution_summary: str | None
    started_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True)
class AgentRunSnapshot:
    accessible: bool
    run: PersistedAgentRun | None


async def claim_agent_run(
    db: AsyncSession,
    *,
    installation_id: uuid.UUID,
    investigation_id: uuid.UUID,
    agent_model: str,
) -> AgentRunClaim:
    """Authorize and atomically claim the one current run row."""
    row = (
        await db.execute(
            select(
                models.Investigation,
                models.Project,
                models.GitHubInstallation,
                models.InvestigationAnalysis,
            )
            .join(models.Project, models.Investigation.project_id == models.Project.id)
            .join(
                models.GitHubInstallation,
                models.Project.github_installation_id == models.GitHubInstallation.id,
            )
            .outerjoin(
                models.InvestigationAnalysis,
                models.InvestigationAnalysis.investigation_id
                == models.Investigation.id,
            )
            .where(
                models.Investigation.id == investigation_id,
                models.Project.github_installation_id == installation_id,
            )
            .with_for_update(of=models.Investigation)
        )
    ).first()
    if row is None:
        return AgentRunClaim(state=AgentRunClaimState.NOT_FOUND)

    investigation, project, installation, analysis = row
    if (
        investigation.status != models.InvestigationStatus.COMPLETED.value
        or analysis is None
    ):
        return AgentRunClaim(state=AgentRunClaimState.NO_ANALYSIS)

    run = (
        await db.execute(
            select(models.InvestigationAgentRun)
            .where(models.InvestigationAgentRun.investigation_id == investigation_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if run is not None and run.status != models.AgentRunStatus.FAILED.value:
        return AgentRunClaim(state=AgentRunClaimState.CONFLICT)

    started_at = datetime.now(UTC)
    if run is None:
        run = models.InvestigationAgentRun(
            investigation_id=investigation_id,
            status=models.AgentRunStatus.RUNNING.value,
            agent_model=agent_model,
            repository_summary=None,
            duplicate_candidates=[],
            reproduction_plan=None,
            generated_test=None,
            reproduction_status=None,
            execution_result=None,
            execution_summary=None,
            execution_error=None,
            started_at=started_at,
            completed_at=None,
        )
        db.add(run)
    else:
        run.status = models.AgentRunStatus.RUNNING.value
        run.agent_model = agent_model
        run.repository_summary = None
        run.duplicate_candidates = []
        run.reproduction_plan = None
        run.generated_test = None
        run.reproduction_status = None
        run.execution_result = None
        run.execution_summary = None
        run.execution_error = None
        run.started_at = started_at
        run.completed_at = None
    await db.flush()

    return AgentRunClaim(
        state=AgentRunClaimState.READY,
        context=AgentRunContext(
            investigation_id=investigation.id,
            github_installation_id=installation.github_installation_id,
            repository_full_name=project.github_repository_full_name,
            default_branch=project.default_branch,
            app_url=project.app_url,
            analysis=_to_bug_analysis(analysis),
        ),
    )


async def complete_agent_run(
    db: AsyncSession,
    *,
    investigation_id: uuid.UUID,
    result: AgentInvestigationResult,
    generated_test: str | None,
    execution: BrowserExecutionResult | None,
) -> PersistedAgentRun:
    run = (
        await db.execute(
            select(models.InvestigationAgentRun)
            .where(
                models.InvestigationAgentRun.investigation_id == investigation_id,
                models.InvestigationAgentRun.status
                == models.AgentRunStatus.RUNNING.value,
            )
            .with_for_update()
        )
    ).scalar_one()
    run.status = models.AgentRunStatus.COMPLETED.value
    run.repository_summary = [
        finding.model_dump(mode="json") for finding in result.repository_findings
    ]
    run.duplicate_candidates = [
        candidate.model_dump(mode="json") for candidate in result.duplicate_candidates
    ]
    run.reproduction_plan = (
        result.reproduction_plan.model_dump(mode="json")
        if result.reproduction_plan is not None
        else None
    )
    run.generated_test = generated_test
    run.reproduction_status = execution.status if execution is not None else None
    run.execution_result = (
        execution.model_dump(mode="json") if execution is not None else None
    )
    run.execution_summary = (
        execution.summary
        if execution is not None
        else result.cannot_reproduce_reason
    )
    run.execution_error = None
    run.completed_at = datetime.now(UTC)
    await db.flush()
    return _to_persisted(run)


async def mark_agent_run_failed(
    db: AsyncSession, *, investigation_id: uuid.UUID
) -> None:
    run = (
        await db.execute(
            select(models.InvestigationAgentRun)
            .where(
                models.InvestigationAgentRun.investigation_id == investigation_id,
                models.InvestigationAgentRun.status
                == models.AgentRunStatus.RUNNING.value,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if run is not None:
        run.status = models.AgentRunStatus.FAILED.value
        run.execution_error = "Investigation failed. Please try again."
        run.completed_at = datetime.now(UTC)


async def get_agent_run(
    db: AsyncSession,
    *,
    installation_id: uuid.UUID,
    investigation_id: uuid.UUID,
) -> AgentRunSnapshot:
    row = (
        await db.execute(
            select(models.Investigation.id, models.InvestigationAgentRun)
            .join(models.Project, models.Investigation.project_id == models.Project.id)
            .outerjoin(
                models.InvestigationAgentRun,
                models.InvestigationAgentRun.investigation_id
                == models.Investigation.id,
            )
            .where(
                models.Investigation.id == investigation_id,
                models.Project.github_installation_id == installation_id,
            )
        )
    ).first()
    if row is None:
        return AgentRunSnapshot(accessible=False, run=None)
    _id, run = row
    return AgentRunSnapshot(
        accessible=True,
        run=_to_persisted(run) if run is not None else None,
    )


def _to_persisted(run: models.InvestigationAgentRun) -> PersistedAgentRun:
    return PersistedAgentRun(
        id=run.id,
        investigation_id=run.investigation_id,
        status=run.status,
        agent_model=run.agent_model,
        repository_summary=run.repository_summary,
        duplicate_candidates=run.duplicate_candidates,
        reproduction_plan=run.reproduction_plan,
        generated_test=run.generated_test,
        reproduction_status=run.reproduction_status,
        execution_result=run.execution_result,
        execution_summary=run.execution_summary,
        started_at=run.started_at,
        completed_at=run.completed_at,
    )


def _to_bug_analysis(analysis: models.InvestigationAnalysis) -> "BugAnalysis":
    from app.investigations.analyzer import BugAnalysis

    return BugAnalysis.model_validate(analysis)
