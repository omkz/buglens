"""Row-locked, installation-scoped persistence for autonomous agent runs."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import select, update
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
    github_issue_status: str | None = None
    github_issue_number: int | None = None
    github_issue_title: str | None = None
    github_issue_url: str | None = None
    progress_stage: str | None = None
    progress_message: str | None = None
    progress_updated_at: datetime | None = None
    run_attempt_id: uuid.UUID | None = None
    fix_proposal: dict | None = None
    fix_proposal_reason: str | None = None


@dataclass(frozen=True)
class PublishedGitHubIssue:
    number: int
    title: str
    url: str


class GitHubIssueClaimState(StrEnum):
    READY = "ready"
    CREATED = "created"
    NOT_FOUND = "not_found"
    NO_COMPLETED_RUN = "no_completed_run"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class GitHubIssuePublicationContext:
    investigation_id: uuid.UUID
    github_installation_id: int
    repository_full_name: str
    investigation_title: str
    investigation_description: str | None
    analysis: "BugAnalysis"
    run: PersistedAgentRun


@dataclass(frozen=True)
class GitHubIssueClaim:
    state: GitHubIssueClaimState
    context: GitHubIssuePublicationContext | None = None
    issue: PublishedGitHubIssue | None = None


@dataclass(frozen=True)
class AgentRunSnapshot:
    accessible: bool
    run: PersistedAgentRun | None


_PUBLICATION_STALE_AFTER = timedelta(minutes=5)


async def claim_agent_run(
    db: AsyncSession,
    *,
    installation_id: uuid.UUID,
    investigation_id: uuid.UUID,
    agent_model: str,
    attempt_id: uuid.UUID,
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
            fix_proposal=None,
            fix_proposal_reason=None,
            reproduction_plan=None,
            generated_test=None,
            reproduction_status=None,
            execution_result=None,
            execution_summary=None,
            execution_error=None,
            github_issue_status=None,
            github_issue_number=None,
            github_issue_title=None,
            github_issue_url=None,
            github_issue_created_at=None,
            github_issue_publish_started_at=None,
            progress_stage=models.AgentRunProgressStage.STARTING.value,
            progress_message="Starting investigation…",
            progress_updated_at=started_at,
            run_attempt_id=attempt_id,
            started_at=started_at,
            completed_at=None,
        )
        db.add(run)
    else:
        run.status = models.AgentRunStatus.RUNNING.value
        run.agent_model = agent_model
        run.repository_summary = None
        run.duplicate_candidates = []
        run.fix_proposal = None
        run.fix_proposal_reason = None
        run.reproduction_plan = None
        run.generated_test = None
        run.reproduction_status = None
        run.execution_result = None
        run.execution_summary = None
        run.execution_error = None
        run.github_issue_status = None
        run.github_issue_number = None
        run.github_issue_title = None
        run.github_issue_url = None
        run.github_issue_created_at = None
        run.github_issue_publish_started_at = None
        run.progress_stage = models.AgentRunProgressStage.STARTING.value
        run.progress_message = "Starting investigation…"
        run.progress_updated_at = started_at
        run.run_attempt_id = attempt_id
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
    attempt_id: uuid.UUID,
    result: AgentInvestigationResult,
    generated_test: str | None,
    execution: BrowserExecutionResult | None,
) -> PersistedAgentRun:
    run = (
        await db.execute(
            select(models.InvestigationAgentRun)
            .where(
                models.InvestigationAgentRun.investigation_id == investigation_id,
                models.InvestigationAgentRun.run_attempt_id == attempt_id,
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
    run.fix_proposal = (
        result.fix_proposal.model_dump(mode="json")
        if result.fix_proposal is not None
        else None
    )
    run.fix_proposal_reason = result.cannot_propose_fix_reason
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
    completed_at = datetime.now(UTC)
    run.completed_at = completed_at
    run.progress_stage = models.AgentRunProgressStage.COMPLETED.value
    run.progress_message = "Investigation completed."
    run.progress_updated_at = completed_at
    await db.flush()
    return _to_persisted(run)


async def mark_agent_run_failed(
    db: AsyncSession, *, investigation_id: uuid.UUID, attempt_id: uuid.UUID
) -> None:
    run = (
        await db.execute(
            select(models.InvestigationAgentRun)
            .where(
                models.InvestigationAgentRun.investigation_id == investigation_id,
                models.InvestigationAgentRun.run_attempt_id == attempt_id,
                models.InvestigationAgentRun.status
                == models.AgentRunStatus.RUNNING.value,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if run is not None:
        failed_at = datetime.now(UTC)
        run.status = models.AgentRunStatus.FAILED.value
        run.execution_error = "Investigation failed. Please try again."
        run.completed_at = failed_at
        run.progress_stage = models.AgentRunProgressStage.FAILED.value
        run.progress_message = "Investigation failed."
        run.progress_updated_at = failed_at


async def update_agent_run_progress(
    db: AsyncSession,
    *,
    investigation_id: uuid.UUID,
    attempt_id: uuid.UUID,
    stage: models.AgentRunProgressStage,
    message: str,
) -> None:
    """Persist one trusted progress snapshot only while the run is active."""
    await db.execute(
        update(models.InvestigationAgentRun)
        .where(
            models.InvestigationAgentRun.investigation_id == investigation_id,
            models.InvestigationAgentRun.run_attempt_id == attempt_id,
            models.InvestigationAgentRun.status == models.AgentRunStatus.RUNNING.value,
        )
        .values(
            progress_stage=stage.value,
            progress_message=message,
            progress_updated_at=datetime.now(UTC),
        )
    )


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


async def claim_github_issue_publication(
    db: AsyncSession,
    *,
    installation_id: uuid.UUID,
    investigation_id: uuid.UUID,
) -> GitHubIssueClaim:
    """Authorize and claim publication without spanning the GitHub request."""
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
        return GitHubIssueClaim(state=GitHubIssueClaimState.NOT_FOUND)

    investigation, project, installation, analysis = row
    run = (
        await db.execute(
            select(models.InvestigationAgentRun)
            .where(models.InvestigationAgentRun.investigation_id == investigation_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if (
        run is None
        or run.status != models.AgentRunStatus.COMPLETED.value
        or analysis is None
    ):
        return GitHubIssueClaim(state=GitHubIssueClaimState.NO_COMPLETED_RUN)

    if (
        run.github_issue_status
        == models.GitHubIssuePublicationStatus.CREATED.value
        and run.github_issue_number is not None
        and run.github_issue_title is not None
        and run.github_issue_url is not None
    ):
        return GitHubIssueClaim(
            state=GitHubIssueClaimState.CREATED,
            issue=PublishedGitHubIssue(
                number=run.github_issue_number,
                title=run.github_issue_title,
                url=run.github_issue_url,
            ),
        )

    now = datetime.now(UTC)
    if (
        run.github_issue_status
        == models.GitHubIssuePublicationStatus.CREATING.value
        and run.github_issue_publish_started_at is not None
        and run.github_issue_publish_started_at > now - _PUBLICATION_STALE_AFTER
    ):
        return GitHubIssueClaim(state=GitHubIssueClaimState.CONFLICT)

    run.github_issue_status = models.GitHubIssuePublicationStatus.CREATING.value
    run.github_issue_publish_started_at = now
    await db.flush()
    persisted = _to_persisted(run)
    return GitHubIssueClaim(
        state=GitHubIssueClaimState.READY,
        context=GitHubIssuePublicationContext(
            investigation_id=investigation.id,
            github_installation_id=installation.github_installation_id,
            repository_full_name=project.github_repository_full_name,
            investigation_title=investigation.title,
            investigation_description=investigation.description,
            analysis=_to_bug_analysis(analysis),
            run=persisted,
        ),
    )


async def complete_github_issue_publication(
    db: AsyncSession,
    *,
    investigation_id: uuid.UUID,
    issue: PublishedGitHubIssue,
) -> PersistedAgentRun:
    run = (
        await db.execute(
            select(models.InvestigationAgentRun)
            .where(
                models.InvestigationAgentRun.investigation_id == investigation_id,
                models.InvestigationAgentRun.github_issue_status
                == models.GitHubIssuePublicationStatus.CREATING.value,
            )
            .with_for_update()
        )
    ).scalar_one()
    run.github_issue_status = models.GitHubIssuePublicationStatus.CREATED.value
    run.github_issue_number = issue.number
    run.github_issue_title = issue.title
    run.github_issue_url = issue.url
    run.github_issue_created_at = datetime.now(UTC)
    await db.flush()
    return _to_persisted(run)


async def mark_github_issue_publication_failed(
    db: AsyncSession, *, investigation_id: uuid.UUID
) -> None:
    run = (
        await db.execute(
            select(models.InvestigationAgentRun)
            .where(
                models.InvestigationAgentRun.investigation_id == investigation_id,
                models.InvestigationAgentRun.github_issue_status
                == models.GitHubIssuePublicationStatus.CREATING.value,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if run is not None:
        run.github_issue_status = models.GitHubIssuePublicationStatus.FAILED.value


def _to_persisted(run: models.InvestigationAgentRun) -> PersistedAgentRun:
    return PersistedAgentRun(
        id=run.id,
        investigation_id=run.investigation_id,
        status=run.status,
        agent_model=run.agent_model,
        repository_summary=run.repository_summary,
        duplicate_candidates=run.duplicate_candidates,
        fix_proposal=run.fix_proposal,
        fix_proposal_reason=run.fix_proposal_reason,
        reproduction_plan=run.reproduction_plan,
        generated_test=run.generated_test,
        reproduction_status=run.reproduction_status,
        execution_result=run.execution_result,
        execution_summary=run.execution_summary,
        started_at=run.started_at,
        completed_at=run.completed_at,
        github_issue_status=run.github_issue_status,
        github_issue_number=run.github_issue_number,
        github_issue_title=run.github_issue_title,
        github_issue_url=run.github_issue_url,
        progress_stage=run.progress_stage,
        progress_message=run.progress_message,
        progress_updated_at=run.progress_updated_at,
        run_attempt_id=run.run_attempt_id,
    )


def _to_bug_analysis(analysis: models.InvestigationAnalysis) -> "BugAnalysis":
    from app.investigations.analyzer import BugAnalysis

    return BugAnalysis.model_validate(analysis)
