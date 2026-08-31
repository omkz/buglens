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
from .schemas import AgentInvestigationResult, BrowserExecutionResult
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
    result: AgentInvestigationResult, github_tools: GitHubToolContext
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
    if result.fix_proposal is None:
        return result
    for change in result.fix_proposal.files:
        if is_forbidden_fix_path(change.path):
            return _without_unverified_fix_proposal(result)
        if change.path not in github_tools.known_paths:
            return _without_unverified_fix_proposal(result)
        original = github_tools.read_files.get(change.path)
        if original is None:
            return _without_unverified_fix_proposal(result)
        if change.original_content != original:
            return _without_unverified_fix_proposal(result)
        if (
            len(change.original_content.encode("utf-8")) > MAX_FIX_CONTENT_BYTES
            or len(change.updated_content.encode("utf-8")) > MAX_FIX_CONTENT_BYTES
        ):
            return _without_unverified_fix_proposal(result)
    return result


def _without_unverified_fix_proposal(
    result: AgentInvestigationResult,
) -> AgentInvestigationResult:
    return result.model_copy(
        update={
            "fix_proposal": None,
            "cannot_propose_fix_reason": _UNVERIFIED_FIX_PROPOSAL_REASON,
        }
    )
