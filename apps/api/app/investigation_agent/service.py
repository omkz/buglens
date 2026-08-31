"""Orchestrate token-scoped ADK reasoning and deterministic browser execution."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import httpx

from app.config import Settings
from app.integrations.github import client as github_client
from app.integrations.github.access import create_scoped_installation_token

from .agent import AgentConfigurationError, RepositoryInvestigationAgent
from .fixes import MAX_FIX_CONTENT_BYTES, is_forbidden_fix_path
from .repository import AgentRunContext
from .schemas import (
    AgentInvestigationDraftResult,
    AgentInvestigationResult,
    BrowserExecutionResult,
    FixProposal,
    ProposedFileChange,
)
from .tools.github import GitHubToolContext
from .tools.playwright import (
    PlaywrightPlanRunner,
    UnsafeApplicationUrlError,
    render_playwright_source,
    validated_app_origin,
)


class InvestigationGitHubError(RuntimeError):
    """Raised when server-scoped GitHub access is unavailable."""


class InvestigationResultError(RuntimeError):
    """Raised when agent output violates trusted server-side constraints."""


ProgressCallback = Callable[[str, str], Awaitable[None]]
_UNVERIFIED_FIX_PROPOSAL_REASON = (
    "The proposed fix could not be safely verified against the retrieved "
    "repository files."
)


class InvestigationAgentService:
    """Run one autonomous investigation without holding a database transaction."""

    def __init__(
        self,
        *,
        agent: RepositoryInvestigationAgent,
        runner: PlaywrightPlanRunner,
        settings: Settings,
    ):
        self.agent = agent
        self.runner = runner
        self.settings = settings

    @property
    def model_name(self) -> str:
        return self.agent.model_name

    async def investigate(
        self,
        context: AgentRunContext,
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[AgentInvestigationResult, str | None, BrowserExecutionResult | None]:
        if progress_callback is not None:
            await progress_callback("starting", "Starting investigation…")
        if (
            not self.settings.google_cloud_project
            or not self.settings.google_cloud_location
        ):
            raise AgentConfigurationError("Vertex AI is not configured.")
        if progress_callback is not None:
            await progress_callback(
                "investigating_repository", "Inspecting repository…"
            )
        try:
            installation_token = await create_scoped_installation_token(
                settings=self.settings,
                github_installation_id=context.github_installation_id,
            )
        except (github_client.GitHubAPIError, httpx.HTTPError) as exc:
            raise InvestigationGitHubError("GitHub is unavailable.") from exc

        try:
            try:
                github_tools = GitHubToolContext(
                    installation_token=installation_token,
                    repository_full_name=context.repository_full_name,
                    default_branch=context.default_branch,
                    progress_callback=progress_callback,
                )
            except ValueError as exc:
                raise InvestigationResultError(
                    "Persisted repository metadata is invalid."
                ) from exc
            result = await self.agent.investigate(
                investigation_id=context.investigation_id,
                analysis=context.analysis,
                application_url_configured=context.app_url is not None,
                tools=github_tools.tools(),
            )
            if github_tools.had_github_failure and not github_tools.known_paths:
                raise InvestigationGitHubError("GitHub is unavailable.")
            result = _validate_agent_result(result, github_tools)
        finally:
            del installation_token

        if result.reproduction_plan is None:
            return result, None, None
        if progress_callback is not None:
            await progress_callback(
                "preparing_reproduction", "Preparing browser reproduction…"
            )
        if context.app_url is None:
            raise InvestigationResultError(
                "Agent returned a browser plan without an application URL."
            )
        try:
            validated_app_origin(
                context.app_url,
                allow_private_network=self.settings.playwright_allow_private_network,
            )
            generated_test = render_playwright_source(
                result.reproduction_plan,
                app_url=context.app_url,
                allow_private_network=self.settings.playwright_allow_private_network,
            )
        except UnsafeApplicationUrlError as exc:
            raise InvestigationResultError("Application URL is not safe.") from exc
        if progress_callback is not None:
            await progress_callback(
                "running_browser", "Running browser reproduction…"
            )
        execution = await self.runner.run(
            result.reproduction_plan,
            app_url=context.app_url,
        )
        return result, generated_test, execution


def _validate_agent_result(
    result: AgentInvestigationDraftResult, github_tools: GitHubToolContext
) -> AgentInvestigationResult:
    for finding in result.repository_findings:
        if finding.path not in github_tools.read_paths:
            raise InvestigationResultError(
                "Agent cited a file it did not read through the scoped tool."
            )
    for candidate in result.duplicate_candidates:
        issue = github_tools.returned_issues.get(candidate.issue_number)
        if (
            issue is None
            or candidate.url != issue.html_url
            or candidate.title != issue.title
        ):
            raise InvestigationResultError(
                "Agent cited an issue that was not returned by the scoped search."
            )
    fix_proposal = _normalize_fix_proposal(result, github_tools)
    fix_reason = result.cannot_propose_fix_reason
    if result.fix_proposal is not None and fix_proposal is None:
        fix_reason = _UNVERIFIED_FIX_PROPOSAL_REASON
    return AgentInvestigationResult(
        repository_findings=result.repository_findings,
        duplicate_candidates=result.duplicate_candidates,
        reproduction_plan=result.reproduction_plan,
        cannot_reproduce_reason=result.cannot_reproduce_reason,
        fix_proposal=fix_proposal,
        cannot_propose_fix_reason=fix_reason,
    )


def _normalize_fix_proposal(
    result: AgentInvestigationDraftResult,
    github_tools: GitHubToolContext,
) -> FixProposal | None:
    if result.fix_proposal is None:
        return None
    normalized_changes: list[ProposedFileChange] = []
    for change in result.fix_proposal.files:
        if is_forbidden_fix_path(change.path):
            return None
        if change.path not in github_tools.known_paths:
            return None
        if change.path not in github_tools.read_paths:
            return None
        original = github_tools.read_files.get(change.path)
        if original is None:
            return None
        try:
            original_size = len(original.encode("utf-8"))
            updated_size = len(change.updated_content.encode("utf-8"))
        except UnicodeError:
            return None
        if (
            original_size > MAX_FIX_CONTENT_BYTES
            or updated_size > MAX_FIX_CONTENT_BYTES
        ):
            return None
        if change.updated_content == original:
            return None
        try:
            normalized_changes.append(
                ProposedFileChange(
                    path=change.path,
                    original_content=original,
                    updated_content=change.updated_content,
                    explanation=change.explanation,
                )
            )
        except ValueError:
            return None
    try:
        return FixProposal(
            summary=result.fix_proposal.summary,
            files=normalized_changes,
        )
    except ValueError:
        return None
