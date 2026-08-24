from __future__ import annotations

import asyncio
import json
import uuid
from base64 import b64encode
from datetime import datetime, timezone
from unittest.mock import Mock

import itsdangerous
import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import NoResultFound, SQLAlchemyError

from app.config import get_settings
from app.db import models
from app.db.session import SessionLocal, engine
from app.integrations.github.repository import (
    PersistedGitHubConnection,
    persist_github_connection,
)
from app.investigation_agent.repository import (
    AgentRunClaimState,
    GitHubIssueClaimState,
    PublishedGitHubIssue,
    claim_agent_run,
    claim_github_issue_publication,
    complete_github_issue_publication,
    complete_agent_run,
    get_agent_run,
    mark_github_issue_publication_failed,
    mark_agent_run_failed,
    update_agent_run_progress,
)
from app.investigation_agent.schemas import AgentInvestigationResult
from app.investigations.analyzer import BugAnalysis
from app.investigations.repository import (
    AnalysisClaimState,
    EvidenceDraft,
    PersistedInvestigation,
    claim_analysis,
    complete_analysis,
    create_evidence_items,
    create_investigation,
    get_investigation,
    list_evidence_items,
    list_investigations,
    mark_analysis_failed,
)
from app.projects.repository import create_project


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


def _signed_session_cookie(connection_id: str) -> str:
    signer = itsdangerous.TimestampSigner(get_settings().session_secret)
    payload = b64encode(json.dumps({"github_connection_id": connection_id}).encode())
    return signer.sign(payload).decode()


def _connection() -> PersistedGitHubConnection:
    return PersistedGitHubConnection(
        connection_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        installation_id=uuid.uuid4(),
        github_installation_id=987654,
        account_login="octo-org",
    )


def _investigation() -> PersistedInvestigation:
    return PersistedInvestigation(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        project_name="Checkout Demo",
        github_repository_full_name="octo-org/checkout",
        title="Checkout button does nothing",
        description="Happens after adding an item to cart.",
        status="pending",
        created_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
    )


def _connected_client(monkeypatch, connection: PersistedGitHubConnection):
    from fastapi.testclient import TestClient

    from app.investigations import routes
    from app.main import app

    async def lookup(*args, **kwargs):
        return connection

    monkeypatch.setattr(routes, "get_connection_by_id", lookup)
    client = TestClient(app)
    client.cookies.set(
        "buglens_session", _signed_session_cookie(str(connection.connection_id))
    )
    return client


def test_create_investigation_requires_session():
    from fastapi.testclient import TestClient

    from app.main import app

    response = TestClient(app).post(
        f"/api/projects/{uuid.uuid4()}/investigations",
        json={"title": "Checkout button does nothing"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "GitHub is not connected."}


def test_create_investigation_rejects_malformed_connection_id():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    client.cookies.set("buglens_session", _signed_session_cookie("not-a-uuid"))
    response = client.post(
        f"/api/projects/{uuid.uuid4()}/investigations",
        json={"title": "Checkout button does nothing"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "GitHub is not connected."}


def test_create_investigation_rejects_missing_persisted_connection(monkeypatch):
    from fastapi.testclient import TestClient

    from app.investigations import routes
    from app.main import app

    async def missing(*args, **kwargs):
        return None

    monkeypatch.setattr(routes, "get_connection_by_id", missing)
    client = TestClient(app)
    client.cookies.set("buglens_session", _signed_session_cookie(str(uuid.uuid4())))
    response = client.post(
        f"/api/projects/{uuid.uuid4()}/investigations",
        json={"title": "Checkout button does nothing"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "GitHub is not connected."}


def test_authenticated_creation_is_scoped_and_defaults_to_pending(monkeypatch):
    from app.investigations import routes

    connection = _connection()
    persisted = _investigation()
    captured: dict[str, object] = {}

    async def persist(db, **kwargs):
        captured.update(kwargs)
        return persisted

    monkeypatch.setattr(routes, "persist_investigation", persist)
    client = _connected_client(monkeypatch, connection)
    response = client.post(
        f"/api/projects/{persisted.project_id}/investigations",
        json={
            "title": "Checkout button does nothing",
            "description": "Happens after adding an item to cart.",
        },
    )

    assert response.status_code == 201
    assert response.json() == {
        "id": str(persisted.id),
        "project_id": str(persisted.project_id),
        "project_name": "Checkout Demo",
        "github_repository_full_name": "octo-org/checkout",
        "title": "Checkout button does nothing",
        "description": "Happens after adding an item to cart.",
        "status": "pending",
        "created_at": "2026-08-16T00:00:00Z",
    }
    assert captured == {
        "installation_id": connection.installation_id,
        "project_id": persisted.project_id,
        "title": "Checkout button does nothing",
        "description": "Happens after adding an item to cart.",
    }


def test_project_from_another_installation_returns_404(monkeypatch):
    from app.investigations import routes

    async def inaccessible(*args, **kwargs):
        return None

    monkeypatch.setattr(routes, "persist_investigation", inaccessible)
    client = _connected_client(monkeypatch, _connection())
    response = client.post(
        f"/api/projects/{uuid.uuid4()}/investigations",
        json={"title": "Checkout button does nothing"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Project not found."}


def test_get_investigations_is_scoped_to_current_installation(monkeypatch):
    from app.investigations import routes

    connection = _connection()
    visible = _investigation()
    captured: dict[str, object] = {}

    async def scoped_list(db, *, installation_id):
        captured["installation_id"] = installation_id
        return [visible]

    monkeypatch.setattr(routes, "load_investigations", scoped_list)
    client = _connected_client(monkeypatch, connection)
    response = client.get("/api/investigations?installation_id=999999")

    assert response.status_code == 200
    assert captured["installation_id"] == connection.installation_id
    assert [item["id"] for item in response.json()["investigations"]] == [
        str(visible.id)
    ]


def test_get_investigation_detail_is_scoped_to_current_installation(monkeypatch):
    from app.investigations import routes

    connection = _connection()
    visible = _investigation()
    captured: dict[str, object] = {}

    async def scoped_detail(db, **kwargs):
        captured.update(kwargs)
        return visible

    monkeypatch.setattr(routes, "load_investigation", scoped_detail)
    client = _connected_client(monkeypatch, connection)
    response = client.get(f"/api/investigations/{visible.id}")

    assert response.status_code == 200
    assert captured == {
        "installation_id": connection.installation_id,
        "investigation_id": visible.id,
    }


def test_inaccessible_investigation_returns_indistinguishable_404(monkeypatch):
    from app.investigations import routes

    async def inaccessible(*args, **kwargs):
        return None

    monkeypatch.setattr(routes, "load_investigation", inaccessible)
    client = _connected_client(monkeypatch, _connection())
    response = client.get(f"/api/investigations/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Investigation not found."}


def test_connection_database_failure_returns_safe_503(monkeypatch):
    from fastapi.testclient import TestClient

    from app.investigations import routes
    from app.main import app

    async def fail(*args, **kwargs):
        raise SQLAlchemyError("private database detail")

    test_logger = Mock()
    monkeypatch.setattr(routes, "get_connection_by_id", fail)
    monkeypatch.setattr(routes, "logger", test_logger)
    client = TestClient(app)
    client.cookies.set("buglens_session", _signed_session_cookie(str(uuid.uuid4())))
    response = client.get("/api/investigations")

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Investigations are temporarily unavailable."
    }
    assert "private database detail" not in response.text
    test_logger.exception.assert_called_once_with(
        "investigations_connection_db_failed"
    )


def test_investigation_database_failure_returns_safe_503(monkeypatch):
    from app.investigations import routes

    async def fail(*args, **kwargs):
        raise SQLAlchemyError("private database detail")

    test_logger = Mock()
    connection = _connection()
    project_id = uuid.uuid4()
    monkeypatch.setattr(routes, "persist_investigation", fail)
    monkeypatch.setattr(routes, "logger", test_logger)
    client = _connected_client(monkeypatch, connection)
    response = client.post(
        f"/api/projects/{project_id}/investigations",
        json={"title": "Checkout button does nothing"},
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Investigations are temporarily unavailable."
    }
    assert "private database detail" not in response.text
    test_logger.exception.assert_called_once_with(
        "investigation_create_failed",
        project_id=str(project_id),
        installation_id=connection.github_installation_id,
    )


def test_investigation_model_stores_no_credentials_or_evidence():
    columns = set(models.Investigation.__table__.columns.keys())
    assert columns.isdisjoint(
        {
            "access_token",
            "installation_token",
            "github_app_jwt",
            "private_key",
            "video",
            "audio",
            "evidence",
        }
    )


@requires_database
def test_repository_scope_default_status_and_project_delete_cascade():
    async def _run() -> None:
        first_user_id = 900_300_001
        first_installation_id = 900_300_002
        second_user_id = 900_300_003
        second_installation_id = 900_300_004

        try:
            async with SessionLocal() as db:
                first_connection = await persist_github_connection(
                    db,
                    github_user_id=first_user_id,
                    github_login="first-user",
                    github_installation_id=first_installation_id,
                    account_login="first-org",
                )
                second_connection = await persist_github_connection(
                    db,
                    github_user_id=second_user_id,
                    github_login="second-user",
                    github_installation_id=second_installation_id,
                    account_login="second-org",
                )
                first_project = await create_project(
                    db,
                    installation_id=first_connection.installation_id,
                    name="First Project",
                    github_repository_id=900_300_005,
                    github_repository_name="first-repo",
                    github_repository_full_name="first-org/first-repo",
                    default_branch="main",
                    app_url=None,
                )
                second_project = await create_project(
                    db,
                    installation_id=second_connection.installation_id,
                    name="Second Project",
                    github_repository_id=900_300_006,
                    github_repository_name="second-repo",
                    github_repository_full_name="second-org/second-repo",
                    default_branch="main",
                    app_url=None,
                )
                await db.commit()

                assert (
                    await create_investigation(
                        db,
                        installation_id=first_connection.installation_id,
                        project_id=second_project.id,
                        title="Must Not Be Created",
                        description=None,
                    )
                    is None
                )

                first_investigation = await create_investigation(
                    db,
                    installation_id=first_connection.installation_id,
                    project_id=first_project.id,
                    title="First Bug",
                    description=None,
                )
                second_investigation = await create_investigation(
                    db,
                    installation_id=second_connection.installation_id,
                    project_id=second_project.id,
                    title="Second Bug",
                    description="Other installation",
                )
                assert first_investigation is not None
                assert second_investigation is not None
                await db.commit()

                assert first_investigation.status == "pending"
                first_evidence_id = uuid.uuid4()
                first_evidence = await create_evidence_items(
                    db,
                    installation_id=first_connection.installation_id,
                    investigation_id=first_investigation.id,
                    items=[
                        EvidenceDraft(
                            id=first_evidence_id,
                            kind="logs",
                            mime_type="text/plain",
                            filename=None,
                            storage_key=None,
                            size_bytes=None,
                            text_content="checkout failed",
                        )
                    ],
                )
                assert first_evidence is not None
                assert (
                    await list_evidence_items(
                        db,
                        installation_id=second_connection.installation_id,
                        investigation_id=first_investigation.id,
                    )
                    is None
                )
                await db.commit()

                claim = await claim_analysis(
                    db,
                    installation_id=first_connection.installation_id,
                    investigation_id=first_investigation.id,
                )
                assert claim.state == AnalysisClaimState.READY
                assert claim.investigation is not None
                assert claim.investigation.status == "running"
                await db.commit()
                await mark_analysis_failed(
                    db, investigation_id=first_investigation.id
                )
                await db.commit()
                failed = await get_investigation(
                    db,
                    installation_id=first_connection.installation_id,
                    investigation_id=first_investigation.id,
                )
                assert failed is not None
                assert failed.status == "failed"

                retry_claim = await claim_analysis(
                    db,
                    installation_id=first_connection.installation_id,
                    investigation_id=first_investigation.id,
                )
                assert retry_claim.state == AnalysisClaimState.READY
                assert retry_claim.investigation is not None
                assert retry_claim.investigation.status == "running"
                await db.commit()
                persisted_analysis = await complete_analysis(
                    db,
                    investigation_id=first_investigation.id,
                    model_name="gemini-test-model",
                    analysis=BugAnalysis(
                        summary="Checkout is unresponsive",
                        observed_behavior="Clicking Checkout has no visible effect.",
                        expected_behavior="Checkout should open.",
                        reproduction_steps=["Click Checkout."],
                        error_signals=[],
                        suspected_components=["checkout UI"],
                        confidence="high",
                        needs_more_information=False,
                        missing_information=[],
                    ),
                )
                await db.commit()
                assert persisted_analysis.investigation_id == first_investigation.id
                completed = await get_investigation(
                    db,
                    installation_id=first_connection.installation_id,
                    investigation_id=first_investigation.id,
                )
                assert completed is not None
                assert completed.status == "completed"

                no_run_publication = await claim_github_issue_publication(
                    db,
                    installation_id=first_connection.installation_id,
                    investigation_id=first_investigation.id,
                )
                assert (
                    no_run_publication.state
                    == GitHubIssueClaimState.NO_COMPLETED_RUN
                )
                await db.rollback()

                first_attempt_id = uuid.uuid4()
                inaccessible_run = await claim_agent_run(
                    db,
                    installation_id=second_connection.installation_id,
                    investigation_id=first_investigation.id,
                    agent_model="gemini-test-model",
                    attempt_id=first_attempt_id,
                )
                assert inaccessible_run.state == AgentRunClaimState.NOT_FOUND

                agent_claim = await claim_agent_run(
                    db,
                    installation_id=first_connection.installation_id,
                    investigation_id=first_investigation.id,
                    agent_model="gemini-test-model",
                    attempt_id=first_attempt_id,
                )
                assert agent_claim.state == AgentRunClaimState.READY
                assert agent_claim.context is not None
                assert agent_claim.context.repository_full_name == "first-org/first-repo"
                await db.commit()
                running_snapshot = await get_agent_run(
                    db,
                    installation_id=first_connection.installation_id,
                    investigation_id=first_investigation.id,
                )
                assert running_snapshot.run is not None
                assert running_snapshot.run.progress_stage == "starting"
                assert running_snapshot.run.progress_message == "Starting investigation…"
                assert running_snapshot.run.progress_updated_at is not None
                assert running_snapshot.run.run_attempt_id == first_attempt_id

                await update_agent_run_progress(
                    db,
                    investigation_id=first_investigation.id,
                    attempt_id=first_attempt_id,
                    stage=models.AgentRunProgressStage.INVESTIGATING_REPOSITORY,
                    message="Inspecting repository…",
                )
                await db.commit()

                concurrent_claim = await claim_agent_run(
                    db,
                    installation_id=first_connection.installation_id,
                    investigation_id=first_investigation.id,
                    agent_model="gemini-test-model",
                    attempt_id=uuid.uuid4(),
                )
                assert concurrent_claim.state == AgentRunClaimState.CONFLICT
                await db.rollback()

                running_publication = await claim_github_issue_publication(
                    db,
                    installation_id=first_connection.installation_id,
                    investigation_id=first_investigation.id,
                )
                assert (
                    running_publication.state
                    == GitHubIssueClaimState.NO_COMPLETED_RUN
                )
                await db.rollback()

                await mark_agent_run_failed(
                    db,
                    investigation_id=first_investigation.id,
                    attempt_id=first_attempt_id,
                )
                await db.commit()
                failed_snapshot = await get_agent_run(
                    db,
                    installation_id=first_connection.installation_id,
                    investigation_id=first_investigation.id,
                )
                assert failed_snapshot.run is not None
                assert failed_snapshot.run.progress_stage == "failed"
                assert failed_snapshot.run.progress_message == "Investigation failed."

                await update_agent_run_progress(
                    db,
                    investigation_id=first_investigation.id,
                    attempt_id=first_attempt_id,
                    stage=models.AgentRunProgressStage.RUNNING_BROWSER,
                    message="Running browser reproduction…",
                )
                await db.commit()
                unchanged_failed = await get_agent_run(
                    db,
                    installation_id=first_connection.installation_id,
                    investigation_id=first_investigation.id,
                )
                assert unchanged_failed.run is not None
                assert unchanged_failed.run.progress_stage == "failed"
                failed_run_publication = await claim_github_issue_publication(
                    db,
                    installation_id=first_connection.installation_id,
                    investigation_id=first_investigation.id,
                )
                assert (
                    failed_run_publication.state
                    == GitHubIssueClaimState.NO_COMPLETED_RUN
                )
                await db.rollback()
                retry_attempt_id = uuid.uuid4()
                retry_run = await claim_agent_run(
                    db,
                    installation_id=first_connection.installation_id,
                    investigation_id=first_investigation.id,
                    agent_model="gemini-test-model",
                    attempt_id=retry_attempt_id,
                )
                assert retry_run.state == AgentRunClaimState.READY
                await db.commit()
                retry_snapshot = await get_agent_run(
                    db,
                    installation_id=first_connection.installation_id,
                    investigation_id=first_investigation.id,
                )
                assert retry_snapshot.run is not None
                assert retry_snapshot.run.progress_stage == "starting"
                assert retry_snapshot.run.progress_message == "Starting investigation…"
                assert retry_snapshot.run.run_attempt_id == retry_attempt_id
                assert retry_snapshot.run.run_attempt_id != first_attempt_id
                await mark_agent_run_failed(
                    db,
                    investigation_id=first_investigation.id,
                    attempt_id=first_attempt_id,
                )
                await db.commit()
                await update_agent_run_progress(
                    db,
                    investigation_id=first_investigation.id,
                    attempt_id=first_attempt_id,
                    stage=models.AgentRunProgressStage.RUNNING_BROWSER,
                    message="Old attempt must not update this run.",
                )
                await db.commit()
                attempt_scoped_snapshot = await get_agent_run(
                    db,
                    installation_id=first_connection.installation_id,
                    investigation_id=first_investigation.id,
                )
                assert attempt_scoped_snapshot.run is not None
                assert attempt_scoped_snapshot.run.progress_stage == "starting"
                with pytest.raises(NoResultFound):
                    await complete_agent_run(
                        db,
                        investigation_id=first_investigation.id,
                        attempt_id=first_attempt_id,
                        result=AgentInvestigationResult(
                            repository_findings=[],
                            duplicate_candidates=[],
                            reproduction_plan=None,
                            cannot_reproduce_reason="Stale attempt.",
                        ),
                        generated_test=None,
                        execution=None,
                    )
                await db.rollback()
                persisted_run = await complete_agent_run(
                    db,
                    investigation_id=first_investigation.id,
                    attempt_id=retry_attempt_id,
                    result=AgentInvestigationResult(
                        repository_findings=[],
                        duplicate_candidates=[],
                        reproduction_plan=None,
                        cannot_reproduce_reason="No app URL configured.",
                    ),
                    generated_test=None,
                    execution=None,
                )
                await db.commit()
                assert persisted_run.status == "completed"
                assert persisted_run.progress_stage == "completed"
                assert persisted_run.progress_message == "Investigation completed."
                run_snapshot = await get_agent_run(
                    db,
                    installation_id=first_connection.installation_id,
                    investigation_id=first_investigation.id,
                )
                assert run_snapshot.accessible is True
                assert run_snapshot.run is not None
                assert run_snapshot.run.id == persisted_run.id

                inaccessible_publication = await claim_github_issue_publication(
                    db,
                    installation_id=second_connection.installation_id,
                    investigation_id=first_investigation.id,
                )
                assert inaccessible_publication.state == GitHubIssueClaimState.NOT_FOUND

                publication_claim = await claim_github_issue_publication(
                    db,
                    installation_id=first_connection.installation_id,
                    investigation_id=first_investigation.id,
                )
                assert publication_claim.state == GitHubIssueClaimState.READY
                assert publication_claim.context is not None
                assert (
                    publication_claim.context.repository_full_name
                    == "first-org/first-repo"
                )
                await db.commit()

                concurrent_publication = await claim_github_issue_publication(
                    db,
                    installation_id=first_connection.installation_id,
                    investigation_id=first_investigation.id,
                )
                assert concurrent_publication.state == GitHubIssueClaimState.CONFLICT
                await db.rollback()

                await mark_github_issue_publication_failed(
                    db, investigation_id=first_investigation.id
                )
                await db.commit()
                retry_publication = await claim_github_issue_publication(
                    db,
                    installation_id=first_connection.installation_id,
                    investigation_id=first_investigation.id,
                )
                assert retry_publication.state == GitHubIssueClaimState.READY
                await db.commit()

                published_issue = PublishedGitHubIssue(
                    number=123,
                    title="Checkout is unresponsive",
                    url="https://github.com/first-org/first-repo/issues/123",
                )
                published_run = await complete_github_issue_publication(
                    db,
                    investigation_id=first_investigation.id,
                    issue=published_issue,
                )
                await db.commit()
                assert published_run.github_issue_status == "created"
                assert published_run.github_issue_number == 123

                existing_publication = await claim_github_issue_publication(
                    db,
                    installation_id=first_connection.installation_id,
                    investigation_id=first_investigation.id,
                )
                assert existing_publication.state == GitHubIssueClaimState.CREATED
                assert existing_publication.issue == published_issue
                await db.rollback()

                refreshed_run = await get_agent_run(
                    db,
                    installation_id=first_connection.installation_id,
                    investigation_id=first_investigation.id,
                )
                assert refreshed_run.run is not None
                assert refreshed_run.run.github_issue_url == published_issue.url

                visible = await list_investigations(
                    db, installation_id=first_connection.installation_id
                )
                assert [item.id for item in visible] == [first_investigation.id]
                assert (
                    await get_investigation(
                        db,
                        installation_id=first_connection.installation_id,
                        investigation_id=second_investigation.id,
                    )
                    is None
                )

                await db.execute(
                    delete(models.Project).where(
                        models.Project.id == first_project.id
                    )
                )
                await db.commit()
                deleted = (
                    await db.execute(
                        select(models.Investigation).where(
                            models.Investigation.id == first_investigation.id
                        )
                    )
                ).scalar_one_or_none()
                assert deleted is None
                deleted_evidence = (
                    await db.execute(
                        select(models.InvestigationEvidence).where(
                            models.InvestigationEvidence.id == first_evidence_id
                        )
                    )
                ).scalar_one_or_none()
                assert deleted_evidence is None
                deleted_analysis = (
                    await db.execute(
                        select(models.InvestigationAnalysis).where(
                            models.InvestigationAnalysis.id == persisted_analysis.id
                        )
                    )
                ).scalar_one_or_none()
                assert deleted_analysis is None
                deleted_agent_run = (
                    await db.execute(
                        select(models.InvestigationAgentRun).where(
                            models.InvestigationAgentRun.id == persisted_run.id
                        )
                    )
                ).scalar_one_or_none()
                assert deleted_agent_run is None
        finally:
            async with SessionLocal() as db:
                await db.execute(
                    delete(models.User).where(
                        models.User.github_user_id.in_([first_user_id, second_user_id])
                    )
                )
                await db.execute(
                    delete(models.GitHubInstallation).where(
                        models.GitHubInstallation.github_installation_id.in_(
                            [first_installation_id, second_installation_id]
                        )
                    )
                )
                await db.commit()

    asyncio.run(_run())
