"""Authenticated APIs for creating and reading bug investigations."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import PurePath
from typing import Literal

import httpx
import structlog
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import models
from app.db.session import SessionLocal, get_db
from app.integrations.github import client as github_client
from app.integrations.github.repository import (
    PersistedGitHubConnection,
    get_connection_by_id,
)
from app.investigation_agent.agent import (
    AdkRepositoryInvestigationAgent,
    AgentConfigurationError,
    AgentProviderError,
)
from app.investigation_agent.github_issue import (
    GitHubIssuePublisher,
    build_github_issue,
)
from app.investigation_agent.pull_request import (
    PullRequestConflictError,
    PullRequestPublisher,
    PullRequestStaleError,
    build_pull_request,
)
from app.investigation_agent.fix_validation import (
    FixValidationContext,
    FixValidationResult,
    FixValidationService,
)
from app.investigation_agent.fixes import render_unified_diff
from app.investigation_agent.repository import (
    AgentRunClaimState,
    FixValidationClaimState,
    GitHubIssueClaimState,
    PullRequestClaimState,
    PersistedAgentRun,
    PublishedGitHubIssue,
    PublishedPullRequest,
    claim_agent_run,
    claim_fix_validation,
    claim_github_issue_publication,
    claim_pull_request_publication,
    complete_github_issue_publication,
    complete_pull_request_publication,
    complete_agent_run,
    complete_fix_validation,
    get_agent_run as load_agent_run,
    mark_github_issue_publication_failed,
    mark_pull_request_publication_terminal,
    mark_agent_run_failed,
    update_agent_run_progress,
)
from app.investigation_agent.schemas import (
    BrowserExecutionResult,
    BrowserTestPlan,
    DuplicateCandidate,
    FixProposal,
    RepositoryFinding,
)
from app.investigation_agent.service import (
    InvestigationAgentService,
    InvestigationGitHubError,
    InvestigationResultError,
)
from app.investigation_agent.tools.playwright import PlaywrightPlanRunner

from .analyzer import (
    AnalyzerConfigurationError,
    AnalyzerEvidenceError,
    AnalyzerProviderError,
    BugAnalysis,
    BugAnalyzer,
    InvestigationAnalyzerService,
)
from .evidence_storage import (
    EmptyRecordingError,
    EvidenceStorage,
    EvidenceStorageError,
    RecordingTooLargeError,
    create_evidence_storage,
)
from .gemini import GeminiBugAnalyzer
from .repository import (
    AnalysisClaimState,
    EvidenceDraft,
    PersistedAnalysis,
    PersistedInvestigation,
    claim_analysis,
    complete_analysis,
    create_evidence_items,
    create_investigation as persist_investigation,
    get_analysis as load_analysis,
    get_investigation as load_investigation,
    get_recording_evidence,
    list_evidence_items,
    list_investigations as load_investigations,
    mark_analysis_failed,
)

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["investigations"])

_SESSION_CONNECTION_ID_KEY = "github_connection_id"
_MAX_LOG_CHARACTERS = 100_000
_RECORDING_MIME_TYPES = {"video/webm", "video/mp4"}


class CreateInvestigationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10_000)


class InvestigationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    project_name: str
    github_repository_full_name: str
    title: str
    description: str | None
    status: models.InvestigationStatus
    created_at: datetime


class InvestigationListResponse(BaseModel):
    investigations: list[InvestigationResponse]


class EvidenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: models.EvidenceKind
    mime_type: str | None
    filename: str | None
    size_bytes: int | None
    text_content: str | None
    created_at: datetime


class EvidenceListResponse(BaseModel):
    evidence: list[EvidenceResponse]


class AnalysisStatusResponse(BaseModel):
    investigation_id: uuid.UUID
    status: models.InvestigationStatus
    analysis: BugAnalysis | None


class ProposedFileChangeResponse(BaseModel):
    path: str
    explanation: str
    diff: str


class FixProposalResponse(BaseModel):
    status: Literal["proposed", "not_proposed"]
    summary: str | None
    files: list[ProposedFileChangeResponse]
    reason: str | None


class AgentRunResultResponse(BaseModel):
    repository_findings: list[RepositoryFinding]
    duplicate_candidates: list[DuplicateCandidate]
    reproduction_plan: BrowserTestPlan | None
    generated_test: str | None
    reproduction_status: models.ReproductionStatus | None
    execution: BrowserExecutionResult | None
    execution_summary: str | None
    fix_proposal: FixProposalResponse


class GitHubIssueResponse(BaseModel):
    number: int
    title: str
    url: str


class AgentRunProgressResponse(BaseModel):
    stage: models.AgentRunProgressStage
    message: str
    updated_at: datetime


class RunAgentInvestigationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: uuid.UUID


class PullRequestResponse(BaseModel):
    number: int
    title: str
    url: str
    branch: str


class AgentRunStatusResponse(BaseModel):
    investigation_id: uuid.UUID
    attempt_id: uuid.UUID | None
    status: models.AgentRunStatus | None
    result: AgentRunResultResponse | None
    progress: AgentRunProgressResponse | None
    github_issue_status: models.GitHubIssuePublicationStatus | None
    github_issue: GitHubIssueResponse | None
    pull_request_status: models.PullRequestPublicationStatus | None
    pull_request: PullRequestResponse | None
    fix_validation: FixValidationResult | None


class GitHubIssuePublicationResponse(BaseModel):
    status: Literal["created"]
    issue: GitHubIssueResponse


class PullRequestPublicationResponse(BaseModel):
    status: Literal["created"]
    pull_request: PullRequestResponse


def get_evidence_storage(
    settings: Settings = Depends(get_settings),
) -> EvidenceStorage:
    return create_evidence_storage(
        settings.evidence_storage_backend,
        str(settings.evidence_storage_dir),
        settings.gcs_bucket,
    )


def get_bug_analyzer(settings: Settings = Depends(get_settings)) -> BugAnalyzer:
    return GeminiBugAnalyzer(
        project=settings.google_cloud_project,
        location=settings.google_cloud_location,
        model_name=settings.gemini_model,
        processing_timeout_seconds=settings.gemini_file_processing_timeout_seconds,
    )


def get_analyzer_service(
    analyzer: BugAnalyzer = Depends(get_bug_analyzer),
    storage: EvidenceStorage = Depends(get_evidence_storage),
) -> InvestigationAnalyzerService:
    return InvestigationAnalyzerService(analyzer, storage)


def get_investigation_agent_service(
    settings: Settings = Depends(get_settings),
) -> InvestigationAgentService:
    agent = AdkRepositoryInvestigationAgent(
        project=settings.google_cloud_project,
        location=settings.google_cloud_location,
        model_name=settings.gemini_model,
    )
    runner = PlaywrightPlanRunner(
        action_timeout_ms=settings.playwright_action_timeout_ms,
        run_timeout_seconds=settings.playwright_run_timeout_seconds,
        allow_private_network=settings.playwright_allow_private_network,
    )
    return InvestigationAgentService(agent=agent, runner=runner, settings=settings)


def get_fix_validation_service(
    settings: Settings = Depends(get_settings),
) -> FixValidationService:
    runner = PlaywrightPlanRunner(
        action_timeout_ms=settings.playwright_action_timeout_ms,
        run_timeout_seconds=settings.playwright_run_timeout_seconds,
        allow_private_network=True,
    )
    return FixValidationService(settings=settings, browser_runner=runner)


def get_github_issue_publisher(
    settings: Settings = Depends(get_settings),
) -> GitHubIssuePublisher:
    return GitHubIssuePublisher(settings)


def get_pull_request_publisher(
    settings: Settings = Depends(get_settings),
) -> PullRequestPublisher:
    return PullRequestPublisher(settings)


@router.post(
    "/projects/{project_id}/investigations",
    response_model=InvestigationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_investigation(
    project_id: uuid.UUID,
    payload: CreateInvestigationRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> PersistedInvestigation:
    connection = await _require_connection(request, db)
    try:
        investigation = await persist_investigation(
            db,
            installation_id=connection.installation_id,
            project_id=project_id,
            title=payload.title,
            description=payload.description or None,
        )
        if investigation is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        await db.commit()
    except HTTPException:
        raise
    except SQLAlchemyError:
        await db.rollback()
        logger.exception(
            "investigation_create_failed",
            project_id=str(project_id),
            installation_id=connection.github_installation_id,
        )
        raise HTTPException(
            status_code=503,
            detail="Investigations are temporarily unavailable.",
        ) from None

    logger.info(
        "investigation_created",
        investigation_id=str(investigation.id),
        project_id=str(project_id),
        installation_id=connection.github_installation_id,
    )
    return investigation


@router.get("/investigations", response_model=InvestigationListResponse)
async def get_investigations(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> InvestigationListResponse:
    connection = await _require_connection(request, db)
    try:
        investigations = await load_investigations(
            db, installation_id=connection.installation_id
        )
    except SQLAlchemyError:
        logger.exception(
            "investigations_list_failed",
            installation_id=connection.github_installation_id,
        )
        raise HTTPException(
            status_code=503,
            detail="Investigations are temporarily unavailable.",
        ) from None
    return InvestigationListResponse(
        investigations=[
            InvestigationResponse.model_validate(investigation)
            for investigation in investigations
        ]
    )


@router.get(
    "/investigations/{investigation_id}", response_model=InvestigationResponse
)
async def get_investigation(
    investigation_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> PersistedInvestigation:
    connection = await _require_connection(request, db)
    try:
        investigation = await load_investigation(
            db,
            installation_id=connection.installation_id,
            investigation_id=investigation_id,
        )
    except SQLAlchemyError:
        logger.exception(
            "investigation_detail_failed",
            investigation_id=str(investigation_id),
            installation_id=connection.github_installation_id,
        )
        raise HTTPException(
            status_code=503,
            detail="Investigations are temporarily unavailable.",
        ) from None
    if investigation is None:
        raise HTTPException(status_code=404, detail="Investigation not found.")
    return investigation


@router.post(
    "/investigations/{investigation_id}/evidence",
    response_model=EvidenceListResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_investigation_evidence(
    investigation_id: uuid.UUID,
    request: Request,
    recording: UploadFile | None = File(default=None),
    logs: str | None = Form(default=None),
    settings: Settings = Depends(get_settings),
    storage: EvidenceStorage = Depends(get_evidence_storage),
    db: AsyncSession = Depends(get_db),
) -> EvidenceListResponse:
    connection = await _require_connection(request, db)
    await _require_investigation(
        db,
        installation_id=connection.installation_id,
        investigation_id=investigation_id,
    )

    has_logs = logs is not None and bool(logs.strip())
    if recording is None and not has_logs:
        raise HTTPException(
            status_code=400,
            detail="Provide a recording or logs.",
        )
    if logs is not None and len(logs) > _MAX_LOG_CHARACTERS:
        raise HTTPException(status_code=413, detail="Logs are too large.")

    drafts: list[EvidenceDraft] = []
    stored_key: str | None = None
    if recording is not None:
        mime_type = (recording.content_type or "").lower().strip()
        if mime_type.split(";", 1)[0] not in _RECORDING_MIME_TYPES:
            raise HTTPException(
                status_code=415,
                detail="Recording must be a WebM or MP4 video.",
            )
        evidence_id = uuid.uuid4()
        try:
            stored = await storage.save_recording(
                recording,
                investigation_id=investigation_id,
                evidence_id=evidence_id,
                mime_type=mime_type,
                max_bytes=settings.max_evidence_upload_bytes,
            )
        except EmptyRecordingError:
            raise HTTPException(status_code=400, detail="Recording is empty.") from None
        except RecordingTooLargeError:
            raise HTTPException(
                status_code=413, detail="Recording is too large."
            ) from None
        except EvidenceStorageError:
            logger.exception(
                "evidence_storage_write_failed",
                investigation_id=str(investigation_id),
            )
            raise HTTPException(
                status_code=503,
                detail="Evidence storage is temporarily unavailable.",
            ) from None
        stored_key = stored.storage_key
        drafts.append(
            EvidenceDraft(
                id=evidence_id,
                kind=models.EvidenceKind.RECORDING.value,
                mime_type=mime_type,
                filename=_safe_filename(recording.filename, mime_type),
                storage_key=stored.storage_key,
                size_bytes=stored.size_bytes,
                text_content=None,
            )
        )

    if has_logs:
        drafts.append(
            EvidenceDraft(
                id=uuid.uuid4(),
                kind=models.EvidenceKind.LOGS.value,
                mime_type="text/plain",
                filename=None,
                storage_key=None,
                size_bytes=None,
                text_content=logs,
            )
        )

    try:
        evidence = await create_evidence_items(
            db,
            installation_id=connection.installation_id,
            investigation_id=investigation_id,
            items=drafts,
        )
        if evidence is None:
            await _delete_stored_recording(storage, stored_key, investigation_id)
            raise HTTPException(status_code=404, detail="Investigation not found.")
        await db.commit()
    except HTTPException:
        raise
    except SQLAlchemyError:
        await db.rollback()
        await _delete_stored_recording(storage, stored_key, investigation_id)
        logger.exception(
            "evidence_create_failed",
            investigation_id=str(investigation_id),
            installation_id=connection.github_installation_id,
        )
        raise HTTPException(
            status_code=503,
            detail="Evidence is temporarily unavailable.",
        ) from None

    logger.info(
        "evidence_created",
        investigation_id=str(investigation_id),
        evidence_count=len(evidence),
        installation_id=connection.github_installation_id,
    )
    return EvidenceListResponse(
        evidence=[EvidenceResponse.model_validate(item) for item in evidence]
    )


@router.get(
    "/investigations/{investigation_id}/evidence",
    response_model=EvidenceListResponse,
)
async def get_investigation_evidence(
    investigation_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> EvidenceListResponse:
    connection = await _require_connection(request, db)
    try:
        evidence = await list_evidence_items(
            db,
            installation_id=connection.installation_id,
            investigation_id=investigation_id,
        )
    except SQLAlchemyError:
        logger.exception(
            "evidence_list_failed",
            investigation_id=str(investigation_id),
            installation_id=connection.github_installation_id,
        )
        raise HTTPException(
            status_code=503,
            detail="Evidence is temporarily unavailable.",
        ) from None
    if evidence is None:
        raise HTTPException(status_code=404, detail="Investigation not found.")
    return EvidenceListResponse(
        evidence=[EvidenceResponse.model_validate(item) for item in evidence]
    )


@router.get(
    "/investigations/{investigation_id}/evidence/{evidence_id}/content",
    response_class=StreamingResponse,
)
async def get_investigation_evidence_content(
    investigation_id: uuid.UUID,
    evidence_id: uuid.UUID,
    request: Request,
    storage: EvidenceStorage = Depends(get_evidence_storage),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    connection = await _require_connection(request, db)
    try:
        evidence = await get_recording_evidence(
            db,
            installation_id=connection.installation_id,
            investigation_id=investigation_id,
            evidence_id=evidence_id,
        )
    except SQLAlchemyError:
        logger.exception(
            "evidence_content_lookup_failed",
            investigation_id=str(investigation_id),
            evidence_id=str(evidence_id),
            installation_id=connection.github_installation_id,
        )
        raise HTTPException(
            status_code=503,
            detail="Evidence is temporarily unavailable.",
        ) from None
    if evidence is None or evidence.storage_key is None:
        raise HTTPException(status_code=404, detail="Evidence not found.")

    try:
        content = await storage.open_content(evidence.storage_key)
    except EvidenceStorageError:
        logger.exception(
            "evidence_storage_read_failed",
            investigation_id=str(investigation_id),
            evidence_id=str(evidence_id),
        )
        raise HTTPException(
            status_code=503,
            detail="Evidence storage is temporarily unavailable.",
        ) from None
    headers = (
        {"content-length": str(evidence.size_bytes)}
        if evidence.size_bytes is not None
        else None
    )
    return StreamingResponse(
        content.chunks,
        media_type=evidence.mime_type or "video/webm",
        headers=headers,
    )


@router.post(
    "/investigations/{investigation_id}/analyze",
    response_model=AnalysisStatusResponse,
)
async def analyze_investigation(
    investigation_id: uuid.UUID,
    request: Request,
    analyzer_service: InvestigationAnalyzerService = Depends(get_analyzer_service),
    db: AsyncSession = Depends(get_db),
) -> AnalysisStatusResponse:
    connection = await _require_connection(request, db)
    try:
        claim = await claim_analysis(
            db,
            installation_id=connection.installation_id,
            investigation_id=investigation_id,
        )
        if claim.state == AnalysisClaimState.NOT_FOUND:
            await db.rollback()
            raise HTTPException(status_code=404, detail="Investigation not found.")
        if claim.state == AnalysisClaimState.CONFLICT:
            await db.rollback()
            raise HTTPException(
                status_code=409,
                detail="Investigation analysis is already running or completed.",
            )
        await db.commit()
    except HTTPException:
        raise
    except SQLAlchemyError:
        await db.rollback()
        logger.exception(
            "analysis_claim_failed",
            investigation_id=str(investigation_id),
            installation_id=connection.github_installation_id,
        )
        raise HTTPException(
            status_code=503,
            detail="Bug analysis is temporarily unavailable.",
        ) from None

    if claim.investigation is None or claim.evidence is None:
        raise HTTPException(
            status_code=503,
            detail="Bug analysis is temporarily unavailable.",
        )

    try:
        analysis = await analyzer_service.analyze(
            claim.investigation,
            claim.evidence,
        )
    except AnalyzerConfigurationError as exc:
        await _best_effort_mark_analysis_failed(db, investigation_id)
        logger.warning(
            "analysis_configuration_missing",
            investigation_id=str(investigation_id),
            exception_type=type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail="Bug analysis is not configured.",
        ) from None
    except AnalyzerEvidenceError as exc:
        await _best_effort_mark_analysis_failed(db, investigation_id)
        logger.warning(
            "analysis_evidence_unavailable",
            investigation_id=str(investigation_id),
            exception_type=type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail="Investigation evidence is temporarily unavailable.",
        ) from None
    except AnalyzerProviderError as exc:
        await _best_effort_mark_analysis_failed(db, investigation_id)
        logger.warning(
            "analysis_provider_failed",
            investigation_id=str(investigation_id),
            exception_type=type(exc).__name__,
        )
        raise HTTPException(
            status_code=502,
            detail="Bug analysis failed. Please try again.",
        ) from None

    try:
        persisted = await complete_analysis(
            db,
            investigation_id=investigation_id,
            model_name=analyzer_service.model_name,
            analysis=analysis,
        )
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        await _best_effort_mark_analysis_failed(db, investigation_id)
        logger.exception(
            "analysis_persistence_failed",
            investigation_id=str(investigation_id),
            installation_id=connection.github_installation_id,
        )
        raise HTTPException(
            status_code=503,
            detail="Bug analysis is temporarily unavailable.",
        ) from None

    return _analysis_response(
        investigation_id,
        models.InvestigationStatus.COMPLETED,
        persisted,
    )


@router.get(
    "/investigations/{investigation_id}/analysis",
    response_model=AnalysisStatusResponse,
)
async def get_investigation_analysis(
    investigation_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AnalysisStatusResponse:
    connection = await _require_connection(request, db)
    try:
        snapshot = await load_analysis(
            db,
            installation_id=connection.installation_id,
            investigation_id=investigation_id,
        )
    except SQLAlchemyError:
        logger.exception(
            "analysis_read_failed",
            investigation_id=str(investigation_id),
            installation_id=connection.github_installation_id,
        )
        raise HTTPException(
            status_code=503,
            detail="Bug analysis is temporarily unavailable.",
        ) from None
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Investigation not found.")
    return _analysis_response(
        investigation_id,
        models.InvestigationStatus(snapshot.status),
        snapshot.analysis,
    )


@router.post(
    "/investigations/{investigation_id}/agent-run",
    response_model=AgentRunStatusResponse,
)
async def run_agent_investigation(
    investigation_id: uuid.UUID,
    payload: RunAgentInvestigationRequest,
    request: Request,
    agent_service: InvestigationAgentService = Depends(
        get_investigation_agent_service
    ),
    db: AsyncSession = Depends(get_db),
) -> AgentRunStatusResponse:
    connection = await _require_connection(request, db)
    try:
        claim = await claim_agent_run(
            db,
            installation_id=connection.installation_id,
            investigation_id=investigation_id,
            agent_model=agent_service.model_name,
            attempt_id=payload.attempt_id,
        )
        if claim.state == AgentRunClaimState.NOT_FOUND:
            await db.rollback()
            raise HTTPException(status_code=404, detail="Investigation not found.")
        if claim.state == AgentRunClaimState.NO_ANALYSIS:
            await db.rollback()
            raise HTTPException(
                status_code=400,
                detail="Analyze the bug evidence before starting investigation.",
            )
        if claim.state == AgentRunClaimState.CONFLICT:
            await db.rollback()
            raise HTTPException(
                status_code=409,
                detail="An investigation run is already running or completed.",
            )
        await db.commit()
    except HTTPException:
        raise
    except SQLAlchemyError:
        await db.rollback()
        logger.exception(
            "agent_run_claim_failed",
            investigation_id=str(investigation_id),
            installation_id=connection.github_installation_id,
        )
        raise HTTPException(
            status_code=503,
            detail="Investigation is temporarily unavailable.",
        ) from None

    if claim.context is None:
        raise HTTPException(
            status_code=503, detail="Investigation is temporarily unavailable."
        )

    try:
        result, generated_test, execution = await agent_service.investigate(
            claim.context,
            progress_callback=_agent_progress_callback(
                investigation_id, payload.attempt_id
            ),
        )
    except AgentConfigurationError as exc:
        await _best_effort_mark_agent_run_failed(
            db, investigation_id, payload.attempt_id
        )
        logger.warning(
            "agent_run_configuration_missing",
            investigation_id=str(investigation_id),
            exception_type=type(exc).__name__,
        )
        raise HTTPException(
            status_code=503, detail="Autonomous investigation is not configured."
        ) from None
    except InvestigationGitHubError as exc:
        await _best_effort_mark_agent_run_failed(
            db, investigation_id, payload.attempt_id
        )
        logger.warning(
            "agent_run_github_failed",
            investigation_id=str(investigation_id),
            exception_type=type(exc).__name__,
        )
        raise HTTPException(
            status_code=502,
            detail="Repository investigation failed. Please try again.",
        ) from None
    except AgentProviderError as exc:
        await _best_effort_mark_agent_run_failed(
            db, investigation_id, payload.attempt_id
        )
        validation_fields = {}
        if exc.validation_error_count is not None:
            validation_fields = {
                "validation_error_count": exc.validation_error_count,
                "validation_error_types": exc.validation_error_types,
                "validation_error_locations": exc.validation_error_locations,
            }
        logger.warning(
            "agent_run_provider_failed",
            investigation_id=str(investigation_id),
            failure_kind=exc.kind,
            exception_type=type(exc).__name__,
            exc_info=True,
            safe_exc_info=True,
            **validation_fields,
        )
        raise HTTPException(
            status_code=502,
            detail="Autonomous investigation failed. Please try again.",
        ) from None
    except InvestigationResultError as exc:
        await _best_effort_mark_agent_run_failed(
            db, investigation_id, payload.attempt_id
        )
        logger.warning(
            "agent_run_result_invalid",
            investigation_id=str(investigation_id),
            exception_type=type(exc).__name__,
            exc_info=True,
            safe_exc_info=True,
        )
        raise HTTPException(
            status_code=502,
            detail="Autonomous investigation failed. Please try again.",
        ) from None

    try:
        persisted = await complete_agent_run(
            db,
            investigation_id=investigation_id,
            attempt_id=payload.attempt_id,
            result=result,
            generated_test=generated_test,
            execution=execution,
        )
        response = _agent_run_response(investigation_id, persisted)
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        await _best_effort_mark_agent_run_failed(
            db, investigation_id, payload.attempt_id
        )
        logger.exception(
            "agent_run_persistence_failed",
            investigation_id=str(investigation_id),
            installation_id=connection.github_installation_id,
        )
        raise HTTPException(
            status_code=503,
            detail="Investigation is temporarily unavailable.",
        ) from None
    return response


@router.get(
    "/investigations/{investigation_id}/agent-run",
    response_model=AgentRunStatusResponse,
)
async def get_agent_investigation(
    investigation_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AgentRunStatusResponse:
    connection = await _require_connection(request, db)
    try:
        snapshot = await load_agent_run(
            db,
            installation_id=connection.installation_id,
            investigation_id=investigation_id,
        )
    except SQLAlchemyError:
        logger.exception(
            "agent_run_read_failed",
            investigation_id=str(investigation_id),
            installation_id=connection.github_installation_id,
        )
        raise HTTPException(
            status_code=503, detail="Investigation is temporarily unavailable."
        ) from None
    if not snapshot.accessible:
        raise HTTPException(status_code=404, detail="Investigation not found.")
    return _agent_run_response(investigation_id, snapshot.run)


@router.post(
    "/investigations/{investigation_id}/fix-validation",
    response_model=AgentRunStatusResponse,
)
async def validate_investigation_fix(
    investigation_id: uuid.UUID,
    request: Request,
    validation_service: FixValidationService = Depends(get_fix_validation_service),
    db: AsyncSession = Depends(get_db),
) -> AgentRunStatusResponse:
    connection = await _require_connection(request, db)
    try:
        claim = await claim_fix_validation(
            db,
            installation_id=connection.installation_id,
            investigation_id=investigation_id,
        )
        if claim.state == FixValidationClaimState.NOT_FOUND:
            raise HTTPException(status_code=404, detail="Investigation not found.")
        if claim.state == FixValidationClaimState.NO_COMPLETED_RUN:
            raise HTTPException(status_code=400, detail="A completed autonomous investigation is required.")
        if claim.state == FixValidationClaimState.NO_FIX_PROPOSAL:
            raise HTTPException(status_code=400, detail="No fix proposal is available to validate.")
        if claim.state == FixValidationClaimState.CONFLICT:
            raise HTTPException(status_code=409, detail="Fix validation is already running.")
        if claim.state == FixValidationClaimState.COMPLETED:
            await db.rollback()
            snapshot = await load_agent_run(db, installation_id=connection.installation_id, investigation_id=investigation_id)
            return _agent_run_response(investigation_id, snapshot.run)
        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    except SQLAlchemyError:
        await db.rollback()
        raise HTTPException(status_code=503, detail="Fix validation is temporarily unavailable.") from None

    if claim.context is None:
        logger.error(
            "fix_validation_context_missing",
            investigation_id=str(investigation_id),
        )
        result = _blocked_fix_validation_result(None)
    else:
        try:
            result = await validation_service.validate(claim.context)
        except Exception as exc:
            logger.warning(
                "fix_validation_execution_failed",
                investigation_id=str(investigation_id),
                exception_type=type(exc).__name__,
                exc_info=True,
                safe_exc_info=True,
            )
            result = _blocked_fix_validation_result(claim.context)
    try:
        run = await complete_fix_validation(db, investigation_id=investigation_id, result=result)
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        logger.exception(
            "fix_validation_persistence_failed",
            investigation_id=str(investigation_id),
        )
        run = await _best_effort_complete_fix_validation(
            db,
            investigation_id,
            result,
        )
        if run is None:
            raise HTTPException(
                status_code=503,
                detail="Fix validation is temporarily unavailable.",
            ) from None
    except Exception as exc:
        await db.rollback()
        logger.warning(
            "fix_validation_completion_failed",
            investigation_id=str(investigation_id),
            exception_type=type(exc).__name__,
            exc_info=True,
            safe_exc_info=True,
        )
        run = await _best_effort_complete_fix_validation(
            db,
            investigation_id,
            _blocked_fix_validation_result(claim.context),
        )
        if run is None:
            raise HTTPException(
                status_code=503,
                detail="Fix validation is temporarily unavailable.",
            ) from None
    return _agent_run_response(investigation_id, run)


@router.get(
    "/investigations/{investigation_id}/agent-run/events",
    response_class=StreamingResponse,
)
async def stream_agent_investigation_progress(
    investigation_id: uuid.UUID,
    attempt_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    connection = await _require_connection(request, db)
    try:
        snapshot = await load_agent_run(
            db,
            installation_id=connection.installation_id,
            investigation_id=investigation_id,
        )
        await db.rollback()
    except SQLAlchemyError:
        await db.rollback()
        logger.exception(
            "agent_run_event_authorization_failed",
            investigation_id=str(investigation_id),
            installation_id=connection.github_installation_id,
        )
        raise HTTPException(
            status_code=503, detail="Investigation progress is temporarily unavailable."
        ) from None
    if not snapshot.accessible:
        raise HTTPException(status_code=404, detail="Investigation not found.")

    return StreamingResponse(
        _agent_run_event_stream(
            request,
            installation_id=connection.installation_id,
            investigation_id=investigation_id,
            attempt_id=attempt_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/investigations/{investigation_id}/github-issue",
    response_model=GitHubIssuePublicationResponse,
)
async def create_investigation_github_issue(
    investigation_id: uuid.UUID,
    request: Request,
    publisher: GitHubIssuePublisher = Depends(get_github_issue_publisher),
    db: AsyncSession = Depends(get_db),
) -> GitHubIssuePublicationResponse:
    """Publish persisted results only after this explicit user request."""
    connection = await _require_connection(request, db)
    try:
        claim = await claim_github_issue_publication(
            db,
            installation_id=connection.installation_id,
            investigation_id=investigation_id,
        )
        if claim.state == GitHubIssueClaimState.NOT_FOUND:
            await db.rollback()
            raise HTTPException(status_code=404, detail="Investigation not found.")
        if claim.state == GitHubIssueClaimState.NO_COMPLETED_RUN:
            await db.rollback()
            raise HTTPException(
                status_code=400,
                detail="Run the investigation before creating a GitHub issue.",
            )
        if claim.state == GitHubIssueClaimState.CONFLICT:
            await db.rollback()
            raise HTTPException(
                status_code=409,
                detail="GitHub issue creation is already in progress.",
            )
        if claim.state == GitHubIssueClaimState.CREATED:
            await db.rollback()
            if claim.issue is None:
                raise HTTPException(
                    status_code=503,
                    detail="GitHub issue publication is temporarily unavailable.",
                )
            return _github_issue_publication_response(claim.issue)
        await db.commit()
    except HTTPException:
        raise
    except SQLAlchemyError:
        await db.rollback()
        logger.exception(
            "github_issue_claim_failed",
            investigation_id=str(investigation_id),
            installation_id=connection.github_installation_id,
        )
        raise HTTPException(
            status_code=503,
            detail="GitHub issue publication is temporarily unavailable.",
        ) from None

    if claim.context is None:
        raise HTTPException(
            status_code=503,
            detail="GitHub issue publication is temporarily unavailable.",
        )
    try:
        draft = build_github_issue(claim.context)
        created = await publisher.publish(claim.context, draft)
    except (github_client.GitHubAPIError, httpx.HTTPError) as exc:
        await _best_effort_mark_github_issue_failed(db, investigation_id)
        logger.warning(
            "github_issue_publish_failed",
            investigation_id=str(investigation_id),
            installation_id=connection.github_installation_id,
            exception_type=type(exc).__name__,
        )
        raise HTTPException(
            status_code=502,
            detail="GitHub issue creation failed. Please try again.",
        ) from None

    issue = PublishedGitHubIssue(
        number=created.number,
        title=created.title,
        url=created.html_url,
    )
    try:
        await complete_github_issue_publication(
            db,
            investigation_id=investigation_id,
            issue=issue,
        )
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        await _best_effort_mark_github_issue_failed(db, investigation_id)
        logger.exception(
            "github_issue_persistence_failed",
            investigation_id=str(investigation_id),
            installation_id=connection.github_installation_id,
            issue_number=issue.number,
        )
        raise HTTPException(
            status_code=503,
            detail="GitHub issue publication is temporarily unavailable.",
        ) from None
    logger.info(
        "github_issue_created",
        investigation_id=str(investigation_id),
        installation_id=connection.github_installation_id,
        issue_number=issue.number,
    )
    return _github_issue_publication_response(issue)


@router.post(
    "/investigations/{investigation_id}/pull-request",
    response_model=PullRequestPublicationResponse,
)
async def create_investigation_pull_request(
    investigation_id: uuid.UUID,
    request: Request,
    publisher: PullRequestPublisher = Depends(get_pull_request_publisher),
    db: AsyncSession = Depends(get_db),
) -> PullRequestPublicationResponse:
    """Publish the persisted trusted proposal only after an explicit request."""
    connection = await _require_connection(request, db)
    try:
        claim = await claim_pull_request_publication(
            db,
            installation_id=connection.installation_id,
            investigation_id=investigation_id,
        )
        if claim.state == PullRequestClaimState.NOT_FOUND:
            await db.rollback()
            raise HTTPException(status_code=404, detail="Investigation not found.")
        if claim.state == PullRequestClaimState.NO_COMPLETED_RUN:
            await db.rollback()
            raise HTTPException(
                status_code=400,
                detail="Complete the autonomous investigation before creating a pull request.",
            )
        if claim.state == PullRequestClaimState.NO_FIX_PROPOSAL:
            await db.rollback()
            raise HTTPException(
                status_code=400,
                detail="No persisted fix proposal is available.",
            )
        if claim.state == PullRequestClaimState.CONFLICT:
            await db.rollback()
            raise HTTPException(
                status_code=409,
                detail="Pull request creation is already in progress.",
            )
        if claim.state == PullRequestClaimState.CREATED:
            await db.rollback()
            if claim.pull_request is None:
                raise HTTPException(
                    status_code=503,
                    detail="Pull request publication is temporarily unavailable.",
                )
            return _pull_request_publication_response(claim.pull_request)
        await db.commit()
    except HTTPException:
        raise
    except SQLAlchemyError:
        await db.rollback()
        logger.exception(
            "pull_request_claim_failed",
            investigation_id=str(investigation_id),
            installation_id=connection.github_installation_id,
        )
        raise HTTPException(
            status_code=503,
            detail="Pull request publication is temporarily unavailable.",
        ) from None

    if claim.context is None:
        await _best_effort_mark_pull_request_terminal(
            db,
            investigation_id,
            models.PullRequestPublicationStatus.FAILED,
        )
        raise HTTPException(
            status_code=503,
            detail="Pull request publication is temporarily unavailable.",
        )

    try:
        draft = build_pull_request(claim.context)
        created = await publisher.publish(claim.context, draft)
    except PullRequestStaleError:
        await _best_effort_mark_pull_request_terminal(
            db,
            investigation_id,
            models.PullRequestPublicationStatus.STALE,
        )
        logger.info(
            "pull_request_proposal_stale",
            investigation_id=str(investigation_id),
            installation_id=connection.github_installation_id,
        )
        raise HTTPException(
            status_code=409,
            detail="The proposed fix is stale because a proposed file has changed.",
        ) from None
    except PullRequestConflictError:
        await _best_effort_mark_pull_request_terminal(
            db,
            investigation_id,
            models.PullRequestPublicationStatus.FAILED,
        )
        logger.warning(
            "pull_request_branch_conflict",
            investigation_id=str(investigation_id),
            installation_id=connection.github_installation_id,
        )
        raise HTTPException(
            status_code=409,
            detail="The Buglensa pull request branch already exists and could not be safely reconciled.",
        ) from None
    except (github_client.GitHubAPIError, httpx.HTTPError) as exc:
        await _best_effort_mark_pull_request_terminal(
            db,
            investigation_id,
            models.PullRequestPublicationStatus.FAILED,
        )
        logger.warning(
            "pull_request_publish_failed",
            investigation_id=str(investigation_id),
            installation_id=connection.github_installation_id,
            exception_type=type(exc).__name__,
        )
        raise HTTPException(
            status_code=502,
            detail="Pull request creation failed. Please try again.",
        ) from None
    except Exception as exc:
        await _best_effort_mark_pull_request_terminal(
            db,
            investigation_id,
            models.PullRequestPublicationStatus.FAILED,
        )
        logger.error(
            "pull_request_publish_unexpected_failure",
            investigation_id=str(investigation_id),
            installation_id=connection.github_installation_id,
            exception_type=type(exc).__name__,
            exc_info=True,
            safe_exc_info=True,
        )
        raise HTTPException(
            status_code=502,
            detail="Pull request creation failed. Please try again.",
        ) from None

    pull_request = PublishedPullRequest(
        number=created.number,
        title=created.title,
        url=created.html_url,
        branch=created.head_branch,
    )
    try:
        await complete_pull_request_publication(
            db,
            investigation_id=investigation_id,
            pull_request=pull_request,
        )
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        await _best_effort_mark_pull_request_terminal(
            db,
            investigation_id,
            models.PullRequestPublicationStatus.FAILED,
        )
        logger.exception(
            "pull_request_persistence_failed",
            investigation_id=str(investigation_id),
            installation_id=connection.github_installation_id,
            pull_request_number=pull_request.number,
        )
        raise HTTPException(
            status_code=503,
            detail="Pull request publication is temporarily unavailable.",
        ) from None
    logger.info(
        "pull_request_created",
        investigation_id=str(investigation_id),
        installation_id=connection.github_installation_id,
        pull_request_number=pull_request.number,
    )
    return _pull_request_publication_response(pull_request)


def _agent_run_response(
    investigation_id: uuid.UUID,
    run: PersistedAgentRun | None,
) -> AgentRunStatusResponse:
    if run is None:
        return AgentRunStatusResponse(
            investigation_id=investigation_id,
            status=None,
            attempt_id=None,
            result=None,
            progress=None,
            github_issue_status=None,
            github_issue=None,
            pull_request_status=None,
            pull_request=None,
            fix_validation=None,
        )
    result = None
    if run.status == models.AgentRunStatus.COMPLETED.value:
        persisted_fix = (
            FixProposal.model_validate(run.fix_proposal)
            if run.fix_proposal is not None
            else None
        )
        result = AgentRunResultResponse(
            repository_findings=[
                RepositoryFinding.model_validate(item)
                for item in (run.repository_summary or [])
            ],
            duplicate_candidates=[
                DuplicateCandidate.model_validate(item)
                for item in run.duplicate_candidates
            ],
            reproduction_plan=(
                BrowserTestPlan.model_validate(run.reproduction_plan)
                if run.reproduction_plan is not None
                else None
            ),
            generated_test=run.generated_test,
            reproduction_status=(
                models.ReproductionStatus(run.reproduction_status)
                if run.reproduction_status is not None
                else None
            ),
            execution=(
                BrowserExecutionResult.model_validate(run.execution_result)
                if run.execution_result is not None
                else None
            ),
            execution_summary=run.execution_summary,
            fix_proposal=(
                FixProposalResponse(
                    status="proposed",
                    summary=persisted_fix.summary,
                    files=[
                        ProposedFileChangeResponse(
                            path=change.path,
                            explanation=change.explanation,
                            diff=render_unified_diff(change),
                        )
                        for change in persisted_fix.files
                    ],
                    reason=None,
                )
                if persisted_fix is not None
                else FixProposalResponse(
                    status="not_proposed",
                    summary=None,
                    files=[],
                    reason=run.fix_proposal_reason,
                )
            ),
        )
    return AgentRunStatusResponse(
        investigation_id=investigation_id,
        status=models.AgentRunStatus(run.status),
        attempt_id=run.run_attempt_id,
        result=result,
        progress=(
            AgentRunProgressResponse(
                stage=models.AgentRunProgressStage(run.progress_stage),
                message=run.progress_message,
                updated_at=run.progress_updated_at,
            )
            if run.progress_stage is not None
            and run.progress_message is not None
            and run.progress_updated_at is not None
            else None
        ),
        github_issue_status=(
            models.GitHubIssuePublicationStatus(run.github_issue_status)
            if run.github_issue_status is not None
            else None
        ),
        github_issue=(
            GitHubIssueResponse(
                number=run.github_issue_number,
                title=run.github_issue_title,
                url=run.github_issue_url,
            )
            if run.github_issue_status
            == models.GitHubIssuePublicationStatus.CREATED.value
            and run.github_issue_number is not None
            and run.github_issue_title is not None
            and run.github_issue_url is not None
            else None
        ),
        pull_request_status=(
            models.PullRequestPublicationStatus(run.pull_request_status)
            if run.pull_request_status is not None
            else None
        ),
        pull_request=(
            PullRequestResponse(
                number=run.pull_request_number,
                title=run.pull_request_title,
                url=run.pull_request_url,
                branch=run.pull_request_branch,
            )
            if run.pull_request_status
            == models.PullRequestPublicationStatus.CREATED.value
            and run.pull_request_number is not None
            and run.pull_request_title is not None
            and run.pull_request_url is not None
            and run.pull_request_branch is not None
            else None
        ),
        fix_validation=(
            FixValidationResult.model_validate(run.fix_validation_result)
            if run.fix_validation_result is not None
            else (
                FixValidationResult(
                    status="running",
                    summary="Fix validation is running.",
                    checks=[],
                    reproduction_before=(
                        run.reproduction_status
                        if run.reproduction_status
                        in {"reproduced", "not_reproduced", "blocked"}
                        else None
                    ),
                    reproduction_after=None,
                )
                if run.fix_validation_status
                == models.FixValidationStatus.RUNNING.value
                else None
            )
        ),
    )


def _agent_progress_callback(
    investigation_id: uuid.UUID, attempt_id: uuid.UUID
):
    async def persist(stage: str, message: str) -> None:
        try:
            progress_stage = models.AgentRunProgressStage(stage)
            async with SessionLocal() as progress_db:
                await update_agent_run_progress(
                    progress_db,
                    investigation_id=investigation_id,
                    attempt_id=attempt_id,
                    stage=progress_stage,
                    message=message,
                )
                await progress_db.commit()
        except Exception as exc:
            logger.warning(
                "agent_run_progress_update_failed",
                investigation_id=str(investigation_id),
                exception_type=type(exc).__name__,
            )

    return persist


async def _agent_run_event_stream(
    request: Request,
    *,
    installation_id: uuid.UUID,
    investigation_id: uuid.UUID,
    attempt_id: uuid.UUID,
    poll_interval_seconds: float = 0.75,
) -> AsyncIterator[str]:
    last_progress: tuple[str, str] | None = None
    polls_since_heartbeat = 0
    while not await request.is_disconnected():
        try:
            async with SessionLocal() as event_db:
                snapshot = await load_agent_run(
                    event_db,
                    installation_id=installation_id,
                    investigation_id=investigation_id,
                )
                await event_db.rollback()
        except SQLAlchemyError as exc:
            logger.warning(
                "agent_run_progress_read_failed",
                investigation_id=str(investigation_id),
                exception_type=type(exc).__name__,
            )
            snapshot = None

        if snapshot is not None and not snapshot.accessible:
            return
        run = snapshot.run if snapshot is not None else None
        if (
            run is not None
            and run.run_attempt_id == attempt_id
            and run.progress_stage is not None
            and run.progress_message is not None
            and run.progress_updated_at is not None
        ):
            progress = (run.progress_stage, run.progress_message)
            if progress != last_progress:
                event = (
                    "complete"
                    if run.progress_stage == models.AgentRunProgressStage.COMPLETED.value
                    else "failed"
                    if run.progress_stage == models.AgentRunProgressStage.FAILED.value
                    else "progress"
                )
                payload = json.dumps(
                    {
                        "attempt_id": str(attempt_id),
                        "stage": run.progress_stage,
                        "message": run.progress_message,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                yield f"event: {event}\ndata: {payload}\n\n"
                last_progress = progress
                polls_since_heartbeat = 0
                if event in {"complete", "failed"}:
                    return

        polls_since_heartbeat += 1
        if polls_since_heartbeat >= 20:
            yield ": keepalive\n\n"
            polls_since_heartbeat = 0
        await asyncio.sleep(poll_interval_seconds)


def _github_issue_publication_response(
    issue: PublishedGitHubIssue,
) -> GitHubIssuePublicationResponse:
    return GitHubIssuePublicationResponse(
        status="created",
        issue=GitHubIssueResponse(
            number=issue.number,
            title=issue.title,
            url=issue.url,
        ),
    )


def _pull_request_publication_response(
    pull_request: PublishedPullRequest,
) -> PullRequestPublicationResponse:
    return PullRequestPublicationResponse(
        status="created",
        pull_request=PullRequestResponse(
            number=pull_request.number,
            title=pull_request.title,
            url=pull_request.url,
            branch=pull_request.branch,
        ),
    )


def _analysis_response(
    investigation_id: uuid.UUID,
    investigation_status: models.InvestigationStatus,
    analysis: PersistedAnalysis | None,
) -> AnalysisStatusResponse:
    return AnalysisStatusResponse(
        investigation_id=investigation_id,
        status=investigation_status,
        analysis=BugAnalysis.model_validate(analysis) if analysis is not None else None,
    )


async def _best_effort_mark_analysis_failed(
    db: AsyncSession, investigation_id: uuid.UUID
) -> None:
    try:
        await mark_analysis_failed(db, investigation_id=investigation_id)
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        logger.exception(
            "analysis_failure_status_update_failed",
            investigation_id=str(investigation_id),
        )


async def _best_effort_mark_agent_run_failed(
    db: AsyncSession, investigation_id: uuid.UUID, attempt_id: uuid.UUID
) -> None:
    try:
        await mark_agent_run_failed(
            db,
            investigation_id=investigation_id,
            attempt_id=attempt_id,
        )
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        logger.exception(
            "agent_run_failure_status_update_failed",
            investigation_id=str(investigation_id),
        )


def _blocked_fix_validation_result(
    context: FixValidationContext | None,
) -> FixValidationResult:
    return FixValidationResult(
        status="blocked",
        summary="Fix validation could not complete safely.",
        checks=[],
        reproduction_before=(
            context.reproduction_before if context is not None else None
        ),
        reproduction_after=None,
    )


async def _best_effort_complete_fix_validation(
    db: AsyncSession,
    investigation_id: uuid.UUID,
    result: FixValidationResult,
) -> PersistedAgentRun | None:
    try:
        run = await complete_fix_validation(
            db,
            investigation_id=investigation_id,
            result=result,
        )
        await db.commit()
        return run
    except Exception as exc:
        await db.rollback()
        logger.warning(
            "fix_validation_terminal_status_update_failed",
            investigation_id=str(investigation_id),
            exception_type=type(exc).__name__,
            exc_info=True,
            safe_exc_info=True,
        )
        return None


async def _best_effort_mark_github_issue_failed(
    db: AsyncSession, investigation_id: uuid.UUID
) -> None:
    try:
        await mark_github_issue_publication_failed(
            db, investigation_id=investigation_id
        )
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        logger.exception(
            "github_issue_failure_status_update_failed",
            investigation_id=str(investigation_id),
        )


async def _best_effort_mark_pull_request_terminal(
    db: AsyncSession,
    investigation_id: uuid.UUID,
    status: models.PullRequestPublicationStatus,
) -> None:
    try:
        await mark_pull_request_publication_terminal(
            db,
            investigation_id=investigation_id,
            status=status,
        )
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        logger.exception(
            "pull_request_terminal_status_update_failed",
            investigation_id=str(investigation_id),
        )


async def _require_investigation(
    db: AsyncSession,
    *,
    installation_id: uuid.UUID,
    investigation_id: uuid.UUID,
) -> PersistedInvestigation:
    try:
        investigation = await load_investigation(
            db,
            installation_id=installation_id,
            investigation_id=investigation_id,
        )
    except SQLAlchemyError:
        logger.exception(
            "evidence_investigation_lookup_failed",
            investigation_id=str(investigation_id),
        )
        raise HTTPException(
            status_code=503,
            detail="Evidence is temporarily unavailable.",
        ) from None
    if investigation is None:
        raise HTTPException(status_code=404, detail="Investigation not found.")
    return investigation


def _safe_filename(filename: str | None, mime_type: str) -> str:
    name = PurePath((filename or "").replace("\\", "/")).name
    name = "".join(character for character in name if character.isprintable()).strip()
    if not name:
        extension = (
            ".mp4" if mime_type.split(";", 1)[0] == "video/mp4" else ".webm"
        )
        name = f"recording{extension}"
    return name[:255]


async def _delete_stored_recording(
    storage: EvidenceStorage,
    storage_key: str | None,
    investigation_id: uuid.UUID,
) -> None:
    if storage_key is None:
        return
    try:
        await storage.delete(storage_key)
    except EvidenceStorageError:
        logger.exception(
            "evidence_storage_cleanup_failed",
            investigation_id=str(investigation_id),
        )


async def _require_connection(
    request: Request, db: AsyncSession
) -> PersistedGitHubConnection:
    raw_connection_id = request.session.get(_SESSION_CONNECTION_ID_KEY)
    if not raw_connection_id:
        raise HTTPException(status_code=401, detail="GitHub is not connected.")
    try:
        connection_id = uuid.UUID(raw_connection_id)
    except (AttributeError, TypeError, ValueError):
        raise HTTPException(
            status_code=401, detail="GitHub is not connected."
        ) from None

    try:
        connection = await get_connection_by_id(db, connection_id=connection_id)
    except SQLAlchemyError:
        logger.exception("investigations_connection_db_failed")
        raise HTTPException(
            status_code=503,
            detail="Investigations are temporarily unavailable.",
        ) from None
    if connection is None:
        raise HTTPException(status_code=401, detail="GitHub is not connected.")
    return connection
