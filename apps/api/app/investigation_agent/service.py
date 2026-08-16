"""Orchestrate token-scoped ADK reasoning and deterministic browser execution."""

from __future__ import annotations

import uuid

import httpx

from app.config import Settings
from app.integrations.github import client as github_client
from app.integrations.github.access import create_scoped_installation_token

from .agent import AgentConfigurationError, RepositoryInvestigationAgent
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
        self, context: AgentRunContext
    ) -> tuple[AgentInvestigationResult, str | None, BrowserExecutionResult | None]:
        if not self.settings.gemini_api_key:
            raise AgentConfigurationError("Gemini is not configured.")
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
            _validate_agent_result(result, github_tools)
        finally:
            del installation_token

        if result.reproduction_plan is None:
            return result, None, None
        if context.app_url is None:
            raise InvestigationResultError(
                "Agent returned a browser plan without an application URL."
            )
        try:
            validated_app_origin(context.app_url)
            generated_test = render_playwright_source(
                result.reproduction_plan,
                app_url=context.app_url,
            )
        except UnsafeApplicationUrlError as exc:
            raise InvestigationResultError("Application URL is not safe.") from exc
        execution = await self.runner.run(
            result.reproduction_plan,
            app_url=context.app_url,
        )
        return result, generated_test, execution


def _validate_agent_result(
    result: AgentInvestigationResult, github_tools: GitHubToolContext
) -> None:
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
