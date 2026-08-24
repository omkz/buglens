from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from app.config import get_settings
from app.integrations.github import client as github_client
from app.integrations.github.repository import PersistedGitHubConnection
from app.investigation_agent.github_issue import (
    MAX_ISSUE_BODY_CHARACTERS,
    GitHubIssuePublisher,
    build_github_issue,
)
from app.investigation_agent.repository import (
    GitHubIssueClaim,
    GitHubIssueClaimState,
    GitHubIssuePublicationContext,
    PersistedAgentRun,
    PublishedGitHubIssue,
)
from app.investigations.analyzer import BugAnalysis


def _created_issue_payload() -> dict[str, object]:
    return {
        "number": 123,
        "title": "Checkout button does not navigate",
        "html_url": "https://github.com/octo-org/checkout/issues/123",
        "state": "open",
    }


def test_create_repository_issue_posts_only_to_fixed_repository(monkeypatch):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json=_created_issue_payload())

    monkeypatch.setattr(
        github_client,
        "_make_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    issue = asyncio.run(
        github_client.create_repository_issue(
            "installation-token",
            owner="octo-org",
            repository="checkout",
            title="Checkout button does not navigate",
            body="Structured body",
        )
    )

    assert issue == github_client.GitHubCreatedIssue(
        number=123,
        title="Checkout button does not navigate",
        html_url="https://github.com/octo-org/checkout/issues/123",
        state="open",
    )
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/repos/octo-org/checkout/issues"
    assert json.loads(requests[0].content) == {
        "title": "Checkout button does not navigate",
        "body": "Structured body",
    }
    assert requests[0].headers["Authorization"] == "Bearer installation-token"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {**_created_issue_payload(), "number": 0},
        {**_created_issue_payload(), "html_url": "https://evil.example/issues/123"},
        {**_created_issue_payload(), "state": "unknown"},
    ],
)
def test_create_repository_issue_rejects_invalid_responses(monkeypatch, payload):
    monkeypatch.setattr(
        github_client,
        "_make_http_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(201, json=payload)
            )
        ),
    )
    with pytest.raises(github_client.GitHubAPIError):
        asyncio.run(
            github_client.create_repository_issue(
                "installation-token",
                owner="octo-org",
                repository="checkout",
                title="Title",
                body="Body",
            )
        )


def test_create_repository_issue_propagates_http_failure_safely(monkeypatch):
    monkeypatch.setattr(
        github_client,
        "_make_http_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(403, json={"message": "denied"})
            )
        ),
    )
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(
            github_client.create_repository_issue(
                "installation-token",
                owner="octo-org",
                repository="checkout",
                title="Title",
                body="Body",
            )
        )


def test_find_repository_issue_by_marker_is_bounded_and_repository_scoped(
    monkeypatch,
):
    requests: list[httpx.Request] = []
    marker = "<!-- buglens-investigation:12345678-1234-5678-9234-567812345678 -->"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    **_created_issue_payload(),
                    "number": 122,
                    "body": marker,
                    "pull_request": {"url": "ignored"},
                },
                {**_created_issue_payload(), "body": f"Body\n\n{marker}"},
            ],
        )

    monkeypatch.setattr(
        github_client,
        "_make_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    issue = asyncio.run(
        github_client.find_repository_issue_by_marker(
            "installation-token",
            owner="octo-org",
            repository="checkout",
            marker=marker,
        )
    )

    assert issue is not None
    assert issue.number == 123
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/repos/octo-org/checkout/issues"
    assert requests[0].url.params["per_page"] == "20"


def _publication_context() -> GitHubIssuePublicationContext:
    investigation_id = uuid.UUID("12345678-1234-5678-9234-567812345678")
    run = PersistedAgentRun(
        id=uuid.uuid4(),
        investigation_id=investigation_id,
        status="completed",
        agent_model="gemini-test-model",
        repository_summary=[
            {
                "path": "app/checkout/page.tsx",
                "reason": "The handler is relevant to @org/team.",
                "observation": "Navigation is not triggered for @octocat.",
            }
        ],
        duplicate_candidates=[
            {
                "issue_number": 98,
                "title": "Cart navigation issue",
                "url": "https://github.com/octo-org/checkout/issues/98",
                "similarity": "medium",
                "reason": "Similar visible behavior.",
            }
        ],
        reproduction_plan={
            "name": "Checkout navigation",
            "start_path": "/cart",
            "actions": [
                {"type": "click", "selector": "text=Checkout"},
                {"type": "expect_url", "value": "/checkout"},
            ],
        },
        generated_test="def test_checkout():\n    assert True\n",
        reproduction_status="reproduced",
        execution_result=None,
        execution_summary="The expected URL was not reached.",
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    return GitHubIssuePublicationContext(
        investigation_id=investigation_id,
        github_installation_id=987654,
        repository_full_name="octo-org/checkout",
        investigation_title="Checkout button does not navigate\x00",
        investigation_description="Happens after adding an item to the cart.",
        analysis=BugAnalysis(
            summary="Checkout remains open.",
            observed_behavior="The cart stays visible after clicking Checkout.",
            expected_behavior="The payment page should open.",
            reproduction_steps=["Open the cart.", "Click Checkout."],
            error_signals=["TypeError in the checkout handler."],
            suspected_components=["checkout UI"],
            confidence="high",
            needs_more_information=False,
            missing_information=[],
        ),
        run=run,
    )


def test_issue_formatter_is_deterministic_structured_and_mention_safe():
    context = _publication_context()
    first = build_github_issue(context)
    second = build_github_issue(context)

    assert first == second
    assert first.title == "Checkout button does not navigate"
    for heading in (
        "## Summary",
        "## Observed behavior",
        "## Expected behavior",
        "## Reproduction steps",
        "## Error signals",
        "## Repository findings",
        "## Reproduction result",
        "## Browser reproduction plan",
        "## Possible duplicates",
        "Generated Playwright reproduction test",
    ):
        assert heading in first.body
    assert "**Reproduced**" in first.body
    assert "#98 — Cart navigation issue — Medium similarity" in first.body
    assert "@\u200borg/team" in first.body
    assert "@\u200boctocat" in first.body
    assert "@org/team" not in first.body
    assert "@octocat" not in first.body
    assert first.marker == (
        "<!-- buglens-investigation:12345678-1234-5678-9234-567812345678 -->"
    )
    assert first.marker in first.body
    assert "storage_key" not in first.body
    assert "text_content" not in first.body
    assert "installation-token" not in first.body


def test_issue_formatter_bounds_and_safely_truncates_generated_test():
    context = _publication_context()
    context = replace(
        context,
        run=replace(context.run, generated_test="x" * 100_000),
    )
    draft = build_github_issue(context)

    assert len(draft.body) <= MAX_ISSUE_BODY_CHARACTERS
    assert "generated test truncated by BugLens" in draft.body
    assert draft.marker in draft.body
    assert draft.body.endswith(draft.marker)


@pytest.mark.parametrize(
    ("status", "wording"),
    [
        (
            "reproduced",
            "BugLens reproduced the reported failure during the browser reproduction attempt.",
        ),
        (
            "not_reproduced",
            "BugLens did not reproduce the reported failure during this attempt.",
        ),
        (
            "blocked",
            "BugLens could not make a reliable reproduction determination.",
        ),
        (None, "BugLens did not run a browser reproduction attempt."),
    ],
)
def test_issue_formatter_uses_factual_reproduction_wording(status, wording):
    context = _publication_context()
    context = replace(
        context,
        run=replace(context.run, reproduction_status=status),
    )
    assert wording in build_github_issue(context).body


def test_publisher_reconciles_marker_without_creating_another_issue(monkeypatch):
    from app.investigation_agent import github_issue as issue_module

    calls = {"find": 0, "create": 0}
    existing = github_client.GitHubCreatedIssue(**_created_issue_payload())

    async def token(**kwargs):
        return "short-lived-token"

    async def find(access_token, **kwargs):
        calls["find"] += 1
        assert access_token == "short-lived-token"
        assert kwargs["owner"] == "octo-org"
        assert kwargs["repository"] == "checkout"
        return existing

    async def create(*args, **kwargs):
        calls["create"] += 1
        raise AssertionError("reconciliation must not create a second issue")

    monkeypatch.setattr(issue_module, "create_scoped_installation_token", token)
    monkeypatch.setattr(github_client, "find_repository_issue_by_marker", find)
    monkeypatch.setattr(github_client, "create_repository_issue", create)
    publisher = GitHubIssuePublisher(get_settings())
    context = _publication_context()

    result = asyncio.run(publisher.publish(context, build_github_issue(context)))

    assert result == existing
    assert calls == {"find": 1, "create": 0}


def test_publisher_creates_exactly_once_when_marker_is_absent(monkeypatch):
    from app.investigation_agent import github_issue as issue_module

    calls = {"find": 0, "create": 0}
    created = github_client.GitHubCreatedIssue(**_created_issue_payload())

    async def token(**kwargs):
        return "short-lived-token"

    async def find(*args, **kwargs):
        calls["find"] += 1
        return None

    async def create(access_token, **kwargs):
        calls["create"] += 1
        assert access_token == "short-lived-token"
        assert kwargs["owner"] == "octo-org"
        assert kwargs["repository"] == "checkout"
        assert "buglens-investigation" in kwargs["body"]
        return created

    monkeypatch.setattr(issue_module, "create_scoped_installation_token", token)
    monkeypatch.setattr(github_client, "find_repository_issue_by_marker", find)
    monkeypatch.setattr(github_client, "create_repository_issue", create)
    publisher = GitHubIssuePublisher(get_settings())
    context = _publication_context()

    result = asyncio.run(publisher.publish(context, build_github_issue(context)))

    assert result == created
    assert calls == {"find": 1, "create": 1}


class _FakeDb:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class _FakePublisher:
    def __init__(self):
        self.calls = []

    async def publish(self, context, draft):
        self.calls.append((context, draft))
        return github_client.GitHubCreatedIssue(**_created_issue_payload())


def _publication_dependencies(monkeypatch):
    from app.investigations import routes

    connection = PersistedGitHubConnection(
        connection_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        installation_id=uuid.uuid4(),
        github_installation_id=987654,
        account_login="octo-org",
    )
    db = _FakeDb()

    async def require_connection(*args, **kwargs):
        return connection

    monkeypatch.setattr(routes, "_require_connection", require_connection)
    return routes, connection, db


@pytest.mark.parametrize(
    "claim_state",
    [GitHubIssueClaimState.NO_COMPLETED_RUN],
)
def test_publication_requires_a_completed_agent_run(monkeypatch, claim_state):
    publisher = _FakePublisher()
    routes, _connection, db = _publication_dependencies(monkeypatch)

    async def claim(*args, **kwargs):
        return GitHubIssueClaim(state=claim_state)

    monkeypatch.setattr(routes, "claim_github_issue_publication", claim)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            routes.create_investigation_github_issue(
                uuid.uuid4(), SimpleNamespace(session={}), publisher, db
            )
        )
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == (
        "Run the investigation before creating a GitHub issue."
    )
    assert publisher.calls == []


def test_publication_is_server_scoped_and_idempotent(monkeypatch):
    publisher = _FakePublisher()
    routes, connection, db = _publication_dependencies(monkeypatch)
    context = _publication_context()
    persisted_issue = PublishedGitHubIssue(
        number=123,
        title="Checkout button does not navigate",
        url="https://github.com/octo-org/checkout/issues/123",
    )
    claims = [
        GitHubIssueClaim(state=GitHubIssueClaimState.READY, context=context),
        GitHubIssueClaim(state=GitHubIssueClaimState.CREATED, issue=persisted_issue),
    ]
    captured = {}

    async def claim(db, **kwargs):
        captured.setdefault("claims", []).append(kwargs)
        return claims.pop(0)

    async def complete(db, **kwargs):
        captured["complete"] = kwargs
        return context.run

    monkeypatch.setattr(routes, "claim_github_issue_publication", claim)
    monkeypatch.setattr(routes, "complete_github_issue_publication", complete)
    first = asyncio.run(
        routes.create_investigation_github_issue(
            context.investigation_id, SimpleNamespace(session={}), publisher, db
        )
    )
    second = asyncio.run(
        routes.create_investigation_github_issue(
            context.investigation_id, SimpleNamespace(session={}), publisher, db
        )
    )

    assert first.model_dump(mode="json") == second.model_dump(mode="json") == {
        "status": "created",
        "issue": {
            "number": 123,
            "title": "Checkout button does not navigate",
            "url": "https://github.com/octo-org/checkout/issues/123",
        },
    }
    assert len(publisher.calls) == 1
    assert publisher.calls[0][0].repository_full_name == "octo-org/checkout"
    assert all(
        item["installation_id"] == connection.installation_id
        for item in captured["claims"]
    )
    assert captured["complete"]["issue"] == persisted_issue
    parameters = inspect.signature(
        routes.create_investigation_github_issue
    ).parameters
    assert not {
        "repository",
        "owner",
        "installation_id",
        "github_token",
        "title",
        "body",
        "agent_service",
        "analyzer",
        "runner",
    }.intersection(parameters)
    from app.main import app

    operation = app.openapi()["paths"][
        "/api/investigations/{investigation_id}/github-issue"
    ]["post"]
    assert "requestBody" not in operation


@pytest.mark.parametrize(
    ("state", "status_code", "detail"),
    [
        (GitHubIssueClaimState.NOT_FOUND, 404, "Investigation not found."),
        (
            GitHubIssueClaimState.CONFLICT,
            409,
            "GitHub issue creation is already in progress.",
        ),
    ],
)
def test_publication_authorization_and_concurrency_are_safe(
    monkeypatch, state, status_code, detail
):
    publisher = _FakePublisher()
    routes, _connection, db = _publication_dependencies(monkeypatch)

    async def claim(*args, **kwargs):
        return GitHubIssueClaim(state=state)

    monkeypatch.setattr(routes, "claim_github_issue_publication", claim)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            routes.create_investigation_github_issue(
                uuid.uuid4(), SimpleNamespace(session={}), publisher, db
            )
        )
    assert exc_info.value.status_code == status_code
    assert exc_info.value.detail == detail
    assert publisher.calls == []


def test_publication_github_failure_is_safe_and_retryable(monkeypatch):
    class FailingPublisher:
        async def publish(self, context, draft):
            raise github_client.GitHubAPIError("private provider response")

    routes, _connection, db = _publication_dependencies(monkeypatch)
    context = _publication_context()
    failed = []

    async def claim(*args, **kwargs):
        return GitHubIssueClaim(
            state=GitHubIssueClaimState.READY,
            context=context,
        )

    async def mark(*args, **kwargs):
        failed.append(kwargs["investigation_id"])

    monkeypatch.setattr(routes, "claim_github_issue_publication", claim)
    monkeypatch.setattr(routes, "mark_github_issue_publication_failed", mark)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            routes.create_investigation_github_issue(
                context.investigation_id,
                SimpleNamespace(session={}),
                FailingPublisher(),
                db,
            )
        )
    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "GitHub issue creation failed. Please try again."
    assert "private provider response" not in exc_info.value.detail
    assert failed == [context.investigation_id]


def test_agent_run_response_restores_persisted_issue_state():
    from app.investigations.routes import _agent_run_response

    context = _publication_context()
    run = replace(
        context.run,
        github_issue_status="created",
        github_issue_number=123,
        github_issue_title="Checkout button does not navigate",
        github_issue_url="https://github.com/octo-org/checkout/issues/123",
    )
    response = _agent_run_response(context.investigation_id, run)

    assert response.github_issue_status == "created"
    assert response.github_issue is not None
    assert response.github_issue.number == 123
    assert response.github_issue.url.endswith("/issues/123")


def test_publication_requires_connected_session():
    from app.investigations import routes

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            routes.create_investigation_github_issue(
                uuid.uuid4(),
                SimpleNamespace(session={}),
                _FakePublisher(),
                _FakeDb(),
            )
        )
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "GitHub is not connected."
