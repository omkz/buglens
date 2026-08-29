"""Deterministic, bounded GitHub issue rendering and publication."""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass

from app.config import Settings
from app.integrations.github import client as github_client
from app.integrations.github.access import create_scoped_installation_token

from .repository import GitHubIssuePublicationContext

MAX_ISSUE_BODY_CHARACTERS = 60_000
MAX_ISSUE_TITLE_CHARACTERS = 200
_MENTION = re.compile(r"@(?=[A-Za-z0-9])")


@dataclass(frozen=True)
class GitHubIssueDraft:
    title: str
    body: str
    marker: str


class GitHubIssuePublisher:
    """Publish only to the repository fixed in the persisted context."""

    def __init__(self, settings: Settings):
        self.settings = settings

    async def publish(
        self, context: GitHubIssuePublicationContext, draft: GitHubIssueDraft
    ) -> github_client.GitHubCreatedIssue:
        owner, repository = _repository_parts(context.repository_full_name)
        installation_token = await create_scoped_installation_token(
            settings=self.settings,
            github_installation_id=context.github_installation_id,
        )
        try:
            existing = await github_client.find_repository_issue_by_marker(
                installation_token,
                owner=owner,
                repository=repository,
                marker=draft.marker,
            )
            if existing is not None:
                return existing
            return await github_client.create_repository_issue(
                installation_token,
                owner=owner,
                repository=repository,
                title=draft.title,
                body=draft.body,
            )
        finally:
            del installation_token


def build_github_issue(
    context: GitHubIssuePublicationContext,
) -> GitHubIssueDraft:
    """Build issue Markdown solely from persisted structured results."""
    marker = investigation_marker(context.investigation_id)
    title = _issue_title(context.investigation_title, context.investigation_id)
    analysis = context.analysis
    run = context.run
    sections: list[str] = []

    summary = _clean_prose(analysis.summary)
    if context.investigation_description:
        summary += (
            "\n\n**Additional context**\n\n"
            + _clean_prose(context.investigation_description)
        )
    _add_section(sections, "Summary", summary, 6_000)
    _add_section(
        sections,
        "Observed behavior",
        _clean_prose(analysis.observed_behavior),
        5_000,
    )
    if analysis.expected_behavior:
        _add_section(
            sections,
            "Expected behavior",
            _clean_prose(analysis.expected_behavior),
            5_000,
        )
    if analysis.reproduction_steps:
        _add_section(
            sections,
            "Reproduction steps",
            _ordered_list(analysis.reproduction_steps),
            6_000,
        )
    if analysis.error_signals:
        _add_section(
            sections,
            "Error signals",
            _unordered_list(analysis.error_signals),
            4_000,
        )
    if run.repository_summary:
        findings = []
        for finding in run.repository_summary:
            path = _inline_code(str(finding.get("path", "Unknown path")))
            observation = _clean_prose(str(finding.get("observation", "")))
            reason = _clean_prose(str(finding.get("reason", "")))
            findings.append(
                f"### `{path}`\n\n{observation}"
                + (f"\n\n{reason}" if reason else "")
            )
        _add_section(
            sections, "Repository findings", "\n\n".join(findings), 10_000
        )

    reproduction = _reproduction_result(run.reproduction_status)
    if run.execution_summary:
        reproduction += "\n\n" + _clean_prose(run.execution_summary)
    _add_section(sections, "Reproduction result", reproduction, 4_000)

    if run.reproduction_plan:
        plan = run.reproduction_plan
        steps = [f"Navigate to `{_inline_code(str(plan.get('start_path', '/')))}`"]
        steps.extend(
            _describe_action(action)
            for action in plan.get("actions", [])
            if isinstance(action, dict)
        )
        _add_section(
            sections,
            "Browser reproduction plan",
            _ordered_list(steps, clean=False),
            7_000,
        )

    if run.duplicate_candidates:
        candidates = []
        for candidate in run.duplicate_candidates:
            number = candidate.get("issue_number")
            candidate_title = _clean_prose(str(candidate.get("title", "")))
            similarity = str(candidate.get("similarity", "")).capitalize()
            reason = _clean_prose(str(candidate.get("reason", "")))
            line = f"#{number} — {candidate_title} — {similarity} similarity"
            candidates.append(line + (f" — {reason}" if reason else ""))
        _add_section(
            sections, "Possible duplicates", _unordered_list(candidates, clean=False), 5_000
        )

    body = "\n\n".join(sections)
    footer = f"\n\n---\n\nGenerated by Buglensa.\n\n{marker}"
    if run.generated_test:
        fence = _code_fence(run.generated_test)
        prefix = (
            "\n\n<details>\n<summary>Generated Playwright reproduction test"
            f"</summary>\n\n{fence}python\n"
        )
        suffix = f"\n{fence}\n\n</details>"
        available = MAX_ISSUE_BODY_CHARACTERS - len(body + prefix + suffix + footer)
        if available > 0:
            code = _truncate_code(run.generated_test, available)
            body += prefix + code + suffix
    body += footer
    if len(body) > MAX_ISSUE_BODY_CHARACTERS:
        available = MAX_ISSUE_BODY_CHARACTERS - len(footer)
        body = _truncate(body[:available], available) + footer
    return GitHubIssueDraft(title=title, body=body, marker=marker)


def investigation_marker(investigation_id: uuid.UUID) -> str:
    return f"<!-- buglens-investigation:{investigation_id} -->"


def _issue_title(value: str, investigation_id: uuid.UUID) -> str:
    title = " ".join(_clean_prose(value).split())
    if not title:
        title = f"Bug investigation {investigation_id}"
    return title[:MAX_ISSUE_TITLE_CHARACTERS].rstrip()


def _clean_prose(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = "".join(
        character
        for character in normalized
        if character in {"\n", "\t"}
        or not unicodedata.category(character).startswith("C")
    )
    return _MENTION.sub("@\u200b", cleaned).strip()


def _inline_code(value: str) -> str:
    return _clean_prose(value).replace("`", "\\`").replace("\n", " ")


def _ordered_list(values: list[str], *, clean: bool = True) -> str:
    return "\n".join(
        f"{index}. {(_clean_prose(value) if clean else value)}"
        for index, value in enumerate(values, start=1)
    )


def _unordered_list(values: list[str], *, clean: bool = True) -> str:
    return "\n".join(
        f"- {(_clean_prose(value) if clean else value)}" for value in values
    )


def _add_section(
    sections: list[str], title: str, content: str, maximum: int
) -> None:
    if content:
        sections.append(f"## {title}\n\n{_truncate(content, maximum)}")


def _truncate(value: str, maximum: int) -> str:
    if len(value) <= maximum:
        return value
    notice = "\n\n_Section truncated by Buglensa._"
    return value[: max(0, maximum - len(notice))].rstrip() + notice


def _truncate_code(value: str, maximum: int) -> str:
    if len(value) <= maximum:
        return value
    notice = "\n# ... generated test truncated by Buglensa ..."
    return value[: max(0, maximum - len(notice))].rstrip() + notice


def _code_fence(value: str) -> str:
    longest = max((len(match) for match in re.findall(r"`+", value)), default=0)
    return "`" * max(3, longest + 1)


def _reproduction_result(status: str | None) -> str:
    if status == "reproduced":
        return (
            "**Reproduced**\n\nBuglensa reproduced the reported failure during "
            "the browser reproduction attempt."
        )
    if status == "not_reproduced":
        return (
            "**Not reproduced**\n\nBuglensa did not reproduce the reported failure "
            "during this attempt."
        )
    if status == "blocked":
        return (
            "**Blocked**\n\nBuglensa could not make a reliable reproduction "
            "determination."
        )
    return "**Not attempted**\n\nBuglensa did not run a browser reproduction attempt."


def _describe_action(action: dict) -> str:
    action_type = action.get("type")
    if action_type == "goto":
        return f"Navigate to `{_inline_code(str(action.get('path', '')))}`"
    if action_type == "click":
        return f"Click `{_inline_code(str(action.get('selector', '')))}`"
    if action_type == "fill":
        return f"Fill `{_inline_code(str(action.get('selector', '')))}`"
    if action_type == "press":
        return (
            f"Press `{_inline_code(str(action.get('key', '')))}` in "
            f"`{_inline_code(str(action.get('selector', '')))}`"
        )
    if action_type == "wait_for":
        return f"Wait for `{_inline_code(str(action.get('selector', '')))}`"
    if action_type == "expect_text":
        return (
            f"Expect `{_inline_code(str(action.get('selector', '')))}` to contain "
            f"`{_inline_code(str(action.get('value', '')))}`"
        )
    if action_type == "expect_visible":
        return f"Expect `{_inline_code(str(action.get('selector', '')))}` to be visible"
    if action_type == "expect_url":
        return f"Expect URL `{_inline_code(str(action.get('value', '')))}`"
    return "Perform a validated browser action"


def _repository_parts(full_name: str) -> tuple[str, str]:
    parts = full_name.split("/")
    if len(parts) != 2 or not all(parts):
        raise github_client.GitHubAPIError("Repository metadata is invalid.")
    return parts[0], parts[1]
