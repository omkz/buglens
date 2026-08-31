from __future__ import annotations

import asyncio
import base64
import inspect
import uuid
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.db import models
from app.integrations.github import client as github_client
from app.integrations.github.repository import PersistedGitHubConnection
from app.investigation_agent.fix_validation import FixValidationResult
from app.investigation_agent.pull_request import (
    PullRequestConflictError,
    PullRequestPublisher,
    PullRequestStaleError,
    build_pull_request,
)
from app.investigation_agent.repository import (
    PublishedPullRequest,
    PullRequestClaim,
    PullRequestClaimState,
    PullRequestPublicationContext,
)
from app.investigation_agent.schemas import FixProposal


BASE_SHA = "1" * 40
TREE_SHA = "2" * 40
NEW_TREE_SHA = "3" * 40
COMMIT_SHA = "4" * 40


def _context(validation_status: str | None = None) -> PullRequestPublicationContext:
    validation = (
        FixValidationResult(
            status=validation_status,
            summary="Persisted validation summary.",
            checks=[],
            reproduction_before=None,
            reproduction_after=None,
        )
        if validation_status is not None
        else None
    )
    return PullRequestPublicationContext(
        investigation_id=uuid.UUID("12345678-1234-4567-8123-123456789abc"),
        github_installation_id=987654,
        repository_full_name="octo-org/checkout",
        default_branch="main",
        investigation_title="Checkout does not navigate",
        fix_proposal=FixProposal.model_validate(
            {
                "summary": "Use the checkout route.",
                "files": [
                    {
                        "path": "src/checkout.ts",
                        "original_content": "export const route = '/cart';\n",
                        "updated_content": "export const route = '/checkout';\n",
                        "explanation": "Navigate to the intended checkout route.",
                    }
                ],
            }
        ),
        fix_validation=validation,
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (None, "Not validated — fix validation has not completed."),
        ("blocked", "Blocked — validation could not complete"),
        ("validation_failed", "Validation failed — one or more"),
        ("validated", "Validated — available bounded checks passed."),
    ],
)
def test_pull_request_body_represents_validation_without_gating(status, expected):
    draft = build_pull_request(_context(status))
    assert expected in draft.body
    assert draft.branch == "buglensa/fix-123456781234"
    assert "<!-- buglensa-fix:12345678-1234-4567-8123-123456789abc -->" in draft.body


def _install_successful_github_fakes(monkeypatch, context, *, existing_pr=None):
    calls: list[tuple[str, object]] = []

    async def token(**kwargs):
        return "short-lived-token"

    async def find_pr(*args, **kwargs):
        calls.append(("find_pr", kwargs))
        return existing_pr

    async def find_branch(*args, **kwargs):
        calls.append(("find_branch", kwargs))
        return None

    async def get_branch(*args, **kwargs):
        calls.append(("get_branch", kwargs))
        return BASE_SHA

    async def get_commit(*args, **kwargs):
        calls.append(("get_commit", kwargs))
        return github_client.GitHubGitCommit(sha=BASE_SHA, tree_sha=TREE_SHA)

    async def get_tree(*args, **kwargs):
        calls.append(("get_tree", kwargs))
        return [
            github_client.GitHubTreeEntry(
                path=change.path,
                mode="100644",
                type="blob",
                sha=str(index + 5) * 40,
            )
            for index, change in enumerate(context.fix_proposal.files)
        ]

    async def read_file(*args, **kwargs):
        calls.append(("read_file", kwargs))
        change = next(
            item for item in context.fix_proposal.files if item.path == kwargs["path"]
        )
        return {
            "type": "file",
            "encoding": "base64",
            "content": base64.b64encode(change.original_content.encode()).decode(),
        }

    async def create_blob(*args, **kwargs):
        calls.append(("create_blob", kwargs))
        return "8" * 40

    async def create_tree(*args, **kwargs):
        calls.append(("create_tree", kwargs))
        return NEW_TREE_SHA

    async def create_commit(*args, **kwargs):
        calls.append(("create_commit", kwargs))
        return COMMIT_SHA

    async def create_branch(*args, **kwargs):
        calls.append(("create_branch", kwargs))

    async def create_pr(*args, **kwargs):
        calls.append(("create_pr", kwargs))
        return github_client.GitHubCreatedPullRequest(
            number=42,
            title=kwargs["title"],
            html_url="https://github.com/octo-org/checkout/pull/42",
            head_branch=kwargs["head_branch"],
            base_branch=kwargs["base_branch"],
            body=kwargs["body"],
        )

    from app.investigation_agent import pull_request

    monkeypatch.setattr(pull_request, "create_scoped_installation_token", token)
    monkeypatch.setattr(github_client, "find_repository_pull_request", find_pr)
    monkeypatch.setattr(github_client, "find_repository_branch_sha", find_branch)
    monkeypatch.setattr(github_client, "get_repository_branch_sha", get_branch)
    monkeypatch.setattr(github_client, "get_repository_git_commit", get_commit)
    monkeypatch.setattr(github_client, "get_repository_git_tree", get_tree)
    monkeypatch.setattr(github_client, "read_repository_file", read_file)
    monkeypatch.setattr(github_client, "create_repository_blob", create_blob)
    monkeypatch.setattr(github_client, "create_repository_tree", create_tree)
    monkeypatch.setattr(github_client, "create_repository_commit", create_commit)
    monkeypatch.setattr(github_client, "create_repository_branch", create_branch)
    monkeypatch.setattr(github_client, "create_repository_pull_request", create_pr)
    return calls


def test_exact_baseline_creates_one_commit_and_branch_from_verified_sha(monkeypatch):
    context = _context()
    context = replace(
        context,
        fix_proposal=FixProposal.model_validate(
            {
                **context.fix_proposal.model_dump(mode="json"),
                "files": [
                    *context.fix_proposal.model_dump(mode="json")["files"],
                    {
                        "path": "src/button.ts",
                        "original_content": "export const label = 'Cart';\n",
                        "updated_content": "export const label = 'Checkout';\n",
                        "explanation": "Match the checkout action.",
                    },
                ],
            }
        ),
    )
    calls = _install_successful_github_fakes(monkeypatch, context)
    created = asyncio.run(
        PullRequestPublisher(SimpleNamespace()).publish(
            context, build_pull_request(context)
        )
    )

    assert created.number == 42
    read_call = next(data for name, data in calls if name == "read_file")
    assert read_call["ref"] == BASE_SHA
    commit_calls = [data for name, data in calls if name == "create_commit"]
    assert len(commit_calls) == 1
    assert commit_calls[0]["parent_sha"] == BASE_SHA
    tree_call = next(data for name, data in calls if name == "create_tree")
    assert len(tree_call["entries"]) == len(context.fix_proposal.files)
    branch_call = next(data for name, data in calls if name == "create_branch")
    assert branch_call["commit_sha"] == COMMIT_SHA
    assert branch_call["branch"] != context.default_branch
    assert not any(name == "update_branch" for name, _data in calls)


def test_changed_baseline_is_stale_and_creates_no_git_objects(monkeypatch):
    context = _context()
    calls = _install_successful_github_fakes(monkeypatch, context)

    async def stale_file(*args, **kwargs):
        calls.append(("read_file", kwargs))
        return {
            "type": "file",
            "encoding": "base64",
            "content": base64.b64encode(b"changed upstream\n").decode(),
        }

    monkeypatch.setattr(github_client, "read_repository_file", stale_file)
    with pytest.raises(PullRequestStaleError):
        asyncio.run(
            PullRequestPublisher(SimpleNamespace()).publish(
                context, build_pull_request(context)
            )
        )
    assert not any(
        name in {"create_blob", "create_tree", "create_commit", "create_branch", "create_pr"}
        for name, _data in calls
    )


def test_existing_remote_pr_reconciles_without_creating_branch(monkeypatch):
    context = _context()
    existing = github_client.GitHubCreatedPullRequest(
        number=42,
        title="Fix: Checkout does not navigate",
        html_url="https://github.com/octo-org/checkout/pull/42",
        head_branch="buglensa/fix-123456781234",
        base_branch="main",
        body=build_pull_request(context).body,
    )
    calls = _install_successful_github_fakes(
        monkeypatch, context, existing_pr=existing
    )
    assert (
        asyncio.run(
            PullRequestPublisher(SimpleNamespace()).publish(
                context, build_pull_request(context)
            )
        )
        == existing
    )
    assert [name for name, _data in calls] == ["find_pr"]


def test_unexpected_existing_branch_is_never_force_updated(monkeypatch):
    context = _context()
    calls = _install_successful_github_fakes(monkeypatch, context)

    async def existing_branch(*args, **kwargs):
        calls.append(("find_branch", kwargs))
        return "9" * 40

    monkeypatch.setattr(github_client, "find_repository_branch_sha", existing_branch)
    with pytest.raises(PullRequestConflictError):
        asyncio.run(
            PullRequestPublisher(SimpleNamespace()).publish(
                context, build_pull_request(context)
            )
        )
    assert not any(name == "create_branch" for name, _data in calls)


def test_safely_reconciled_branch_can_finish_pr_after_partial_failure(monkeypatch):
    context = _context()
    draft = build_pull_request(context)
    calls = _install_successful_github_fakes(monkeypatch, context)

    async def existing_branch(*args, **kwargs):
        calls.append(("find_branch", kwargs))
        return COMMIT_SHA

    async def get_commit(*args, **kwargs):
        calls.append(("get_commit", kwargs))
        if kwargs["commit_sha"] == BASE_SHA:
            return github_client.GitHubGitCommit(sha=BASE_SHA, tree_sha=TREE_SHA)
        return github_client.GitHubGitCommit(
            sha=COMMIT_SHA,
            tree_sha=NEW_TREE_SHA,
            message=draft.commit_message,
            parent_shas=(BASE_SHA,),
        )

    async def get_tree(*args, **kwargs):
        calls.append(("get_tree", kwargs))
        return [
            github_client.GitHubTreeEntry(
                path="src/checkout.ts",
                mode="100644",
                type="blob",
                sha=("5" if kwargs["tree_sha"] == TREE_SHA else "8") * 40,
            )
        ]

    async def read_file(*args, **kwargs):
        calls.append(("read_file", kwargs))
        content = (
            context.fix_proposal.files[0].original_content
            if kwargs["ref"] == BASE_SHA
            else context.fix_proposal.files[0].updated_content
        )
        return {
            "type": "file",
            "encoding": "base64",
            "content": base64.b64encode(content.encode()).decode(),
        }

    monkeypatch.setattr(github_client, "find_repository_branch_sha", existing_branch)
    monkeypatch.setattr(github_client, "get_repository_git_commit", get_commit)
    monkeypatch.setattr(github_client, "get_repository_git_tree", get_tree)
    monkeypatch.setattr(github_client, "read_repository_file", read_file)

    created = asyncio.run(
        PullRequestPublisher(SimpleNamespace()).publish(context, draft)
    )
    assert created.number == 42
    assert not any(
        name in {"create_blob", "create_tree", "create_commit", "create_branch"}
        for name, _data in calls
    )
    assert [name for name, _data in calls].count("create_pr") == 1


class _FakeDb:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def _route_dependencies(monkeypatch):
    from app.investigations import routes

    connection = PersistedGitHubConnection(
        connection_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        installation_id=uuid.uuid4(),
        github_installation_id=987654,
        account_login="octo-org",
    )

    async def require_connection(*args, **kwargs):
        return connection

    monkeypatch.setattr(routes, "_require_connection", require_connection)
    return routes, connection, _FakeDb()


def test_duplicate_request_returns_already_persisted_pull_request(monkeypatch):
    routes, _connection, db = _route_dependencies(monkeypatch)
    persisted = PublishedPullRequest(
        number=42,
        title="Fix: Checkout does not navigate",
        url="https://github.com/octo-org/checkout/pull/42",
        branch="buglensa/fix-123456781234",
    )

    async def claim(*args, **kwargs):
        return PullRequestClaim(
            state=PullRequestClaimState.CREATED, pull_request=persisted
        )

    monkeypatch.setattr(routes, "claim_pull_request_publication", claim)
    response = asyncio.run(
        routes.create_investigation_pull_request(
            uuid.uuid4(), SimpleNamespace(session={}), SimpleNamespace(), db
        )
    )
    assert response.pull_request.number == 42
    assert response.pull_request.branch == persisted.branch
    parameters = inspect.signature(
        routes.create_investigation_pull_request
    ).parameters
    assert not {
        "repository",
        "base_branch",
        "head_branch",
        "files",
        "patch",
        "github_token",
    }.intersection(parameters)
    from app.main import app

    operation = app.openapi()["paths"][
        "/api/investigations/{investigation_id}/pull-request"
    ]["post"]
    assert "requestBody" not in operation


@pytest.mark.parametrize(
    ("state", "status_code"),
    [
        (PullRequestClaimState.NOT_FOUND, 404),
        (PullRequestClaimState.NO_COMPLETED_RUN, 400),
        (PullRequestClaimState.NO_FIX_PROPOSAL, 400),
    ],
)
def test_pull_request_route_rejects_inaccessible_or_missing_state(
    monkeypatch, state, status_code
):
    routes, _connection, db = _route_dependencies(monkeypatch)

    async def claim(*args, **kwargs):
        return PullRequestClaim(state=state)

    monkeypatch.setattr(routes, "claim_pull_request_publication", claim)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            routes.create_investigation_pull_request(
                uuid.uuid4(), SimpleNamespace(session={}), SimpleNamespace(), db
            )
        )
    assert exc_info.value.status_code == status_code


def test_concurrent_pull_request_creation_returns_conflict(monkeypatch):
    routes, _connection, db = _route_dependencies(monkeypatch)

    async def claim(*args, **kwargs):
        return PullRequestClaim(state=PullRequestClaimState.CONFLICT)

    monkeypatch.setattr(routes, "claim_pull_request_publication", claim)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            routes.create_investigation_pull_request(
                uuid.uuid4(), SimpleNamespace(session={}), SimpleNamespace(), db
            )
        )
    assert exc_info.value.status_code == 409


@pytest.mark.parametrize("validation_status", [None, "blocked", "validation_failed"])
def test_route_persists_created_pull_request_regardless_of_validation(
    monkeypatch, validation_status
):
    routes, connection, db = _route_dependencies(monkeypatch)
    context = _context(validation_status)
    completed = []

    async def claim(db, **kwargs):
        assert kwargs["installation_id"] == connection.installation_id
        return PullRequestClaim(state=PullRequestClaimState.READY, context=context)

    async def complete(db, **kwargs):
        completed.append(kwargs["pull_request"])

    class Publisher:
        async def publish(self, claimed_context, draft):
            assert claimed_context.fix_validation == context.fix_validation
            return github_client.GitHubCreatedPullRequest(
                number=42,
                title=draft.title,
                html_url="https://github.com/octo-org/checkout/pull/42",
                head_branch=draft.branch,
                base_branch="main",
                body=draft.body,
            )

    monkeypatch.setattr(routes, "claim_pull_request_publication", claim)
    monkeypatch.setattr(routes, "complete_pull_request_publication", complete)
    response = asyncio.run(
        routes.create_investigation_pull_request(
            context.investigation_id,
            SimpleNamespace(session={}),
            Publisher(),
            db,
        )
    )
    assert response.status == "created"
    assert response.pull_request.number == 42
    assert completed == [
        PublishedPullRequest(
            number=42,
            title=response.pull_request.title,
            url=response.pull_request.url,
            branch=response.pull_request.branch,
        )
    ]


def test_stale_publication_persists_stale_terminal_state(monkeypatch):
    routes, _connection, db = _route_dependencies(monkeypatch)
    context = _context()
    terminal = []

    async def claim(*args, **kwargs):
        return PullRequestClaim(state=PullRequestClaimState.READY, context=context)

    async def mark(*args, **kwargs):
        terminal.append(kwargs["status"])

    class StalePublisher:
        async def publish(self, context, draft):
            raise PullRequestStaleError("untrusted detail")

    monkeypatch.setattr(routes, "claim_pull_request_publication", claim)
    monkeypatch.setattr(routes, "mark_pull_request_publication_terminal", mark)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            routes.create_investigation_pull_request(
                context.investigation_id,
                SimpleNamespace(session={}),
                StalePublisher(),
                db,
            )
        )
    assert exc_info.value.status_code == 409
    assert "untrusted detail" not in exc_info.value.detail
    assert terminal == [models.PullRequestPublicationStatus.STALE]


def test_github_api_failure_persists_retryable_failed_status(monkeypatch):
    routes, _connection, db = _route_dependencies(monkeypatch)
    context = _context()
    terminal = []

    async def claim(*args, **kwargs):
        return PullRequestClaim(state=PullRequestClaimState.READY, context=context)

    async def mark(*args, **kwargs):
        terminal.append(kwargs["status"])

    class FailingPublisher:
        async def publish(self, context, draft):
            raise github_client.GitHubAPIError("private upstream response")

    monkeypatch.setattr(routes, "claim_pull_request_publication", claim)
    monkeypatch.setattr(routes, "mark_pull_request_publication_terminal", mark)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            routes.create_investigation_pull_request(
                context.investigation_id,
                SimpleNamespace(session={}),
                FailingPublisher(),
                db,
            )
        )
    assert exc_info.value.status_code == 502
    assert "private upstream response" not in exc_info.value.detail
    assert terminal == [models.PullRequestPublicationStatus.FAILED]
