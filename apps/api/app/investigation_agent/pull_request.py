"""Explicit, stale-safe publication of persisted fix proposals."""

from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from dataclasses import dataclass

from app.config import Settings
from app.integrations.github import client as github_client
from app.integrations.github.access import create_scoped_installation_token

from .fixes import MAX_FIX_CONTENT_BYTES, is_forbidden_fix_path
from .repository import PullRequestPublicationContext

_MAX_TITLE = 200
_MAX_BODY = 30_000
_MAX_EXPLANATION = 2_000
_MENTION = re.compile(r"@(?=[A-Za-z0-9])")


class PullRequestStaleError(RuntimeError):
    """The persisted proposal no longer matches the exact base revision."""


class PullRequestConflictError(RuntimeError):
    """The deterministic remote branch is occupied and cannot be reconciled."""


@dataclass(frozen=True)
class PullRequestDraft:
    title: str
    body: str
    commit_message: str
    branch: str
    marker: str


class PullRequestPublisher:
    """Create one branch, commit, and PR from trusted persisted state only."""

    def __init__(self, settings: Settings):
        self.settings = settings

    async def publish(
        self, context: PullRequestPublicationContext, draft: PullRequestDraft
    ) -> github_client.GitHubCreatedPullRequest:
        owner, repository = _repository_parts(context.repository_full_name)
        installation_token = await create_scoped_installation_token(
            settings=self.settings,
            github_installation_id=context.github_installation_id,
        )
        try:
            existing = await github_client.find_repository_pull_request(
                installation_token,
                owner=owner,
                repository=repository,
                branch=draft.branch,
                base_branch=context.default_branch,
                marker=draft.marker,
            )
            if existing is not None:
                return existing

            existing_branch = await github_client.find_repository_branch_sha(
                installation_token,
                owner=owner,
                repository=repository,
                branch=draft.branch,
            )
            base_sha = await github_client.get_repository_branch_sha(
                installation_token,
                owner=owner,
                repository=repository,
                branch=context.default_branch,
            )
            base_commit = await github_client.get_repository_git_commit(
                installation_token,
                owner=owner,
                repository=repository,
                commit_sha=base_sha,
            )
            if base_commit.sha != base_sha:
                raise github_client.GitHubAPIError(
                    "GitHub returned an inconsistent base commit."
                )
            tree_entries = await github_client.get_repository_git_tree(
                installation_token,
                owner=owner,
                repository=repository,
                tree_sha=base_commit.tree_sha,
            )
            modes = {
                entry.path: entry.mode
                for entry in tree_entries
                if entry.type == "blob" and entry.mode in {"100644", "100755"}
            }

            for change in context.fix_proposal.files:
                _validate_change(change.path, change.original_content, change.updated_content)
                if change.path not in modes:
                    raise PullRequestStaleError("A proposed file no longer exists.")
                payload = await github_client.read_repository_file(
                    installation_token,
                    owner=owner,
                    repository=repository,
                    path=change.path,
                    ref=base_sha,
                )
                if _decode_repository_content(payload) != change.original_content:
                    raise PullRequestStaleError(
                        "A proposed file changed after the investigation."
                    )

            if existing_branch is not None:
                await _verify_existing_branch(
                    installation_token,
                    owner=owner,
                    repository=repository,
                    context=context,
                    draft=draft,
                    base_sha=base_sha,
                    base_entries=tree_entries,
                    branch_sha=existing_branch,
                )
                created = await github_client.create_repository_pull_request(
                    installation_token,
                    owner=owner,
                    repository=repository,
                    title=draft.title,
                    body=draft.body,
                    head_branch=draft.branch,
                    base_branch=context.default_branch,
                )
                return _verified_pull_request(created, draft.marker)

            replacement_entries: list[github_client.GitHubTreeEntry] = []
            for change in context.fix_proposal.files:
                blob_sha = await github_client.create_repository_blob(
                    installation_token,
                    owner=owner,
                    repository=repository,
                    content=change.updated_content,
                )
                replacement_entries.append(
                    github_client.GitHubTreeEntry(
                        path=change.path,
                        mode=modes[change.path],
                        type="blob",
                        sha=blob_sha,
                    )
                )

            tree_sha = await github_client.create_repository_tree(
                installation_token,
                owner=owner,
                repository=repository,
                base_tree_sha=base_commit.tree_sha,
                entries=replacement_entries,
            )
            commit_sha = await github_client.create_repository_commit(
                installation_token,
                owner=owner,
                repository=repository,
                message=draft.commit_message,
                tree_sha=tree_sha,
                parent_sha=base_sha,
            )
            await github_client.create_repository_branch(
                installation_token,
                owner=owner,
                repository=repository,
                branch=draft.branch,
                commit_sha=commit_sha,
            )
            created = await github_client.create_repository_pull_request(
                installation_token,
                owner=owner,
                repository=repository,
                title=draft.title,
                body=draft.body,
                head_branch=draft.branch,
                base_branch=context.default_branch,
            )
            return _verified_pull_request(created, draft.marker)
        finally:
            del installation_token


def build_pull_request(context: PullRequestPublicationContext) -> PullRequestDraft:
    title_text = _clean_inline(context.investigation_title)
    if not title_text:
        title_text = f"investigation {context.investigation_id}"
    title = _truncate(f"Fix: {title_text}", _MAX_TITLE)
    commit_title = _truncate(f"fix: {title_text}", _MAX_TITLE)
    branch = f"buglensa/fix-{context.investigation_id.hex[:12]}"
    marker = f"<!-- buglensa-fix:{context.investigation_id} -->"
    commit_message = f"{commit_title}\n\nBuglensa-Fix: {context.investigation_id}"

    changes = []
    for change in context.fix_proposal.files:
        explanation = _truncate(
            _clean_inline(change.explanation), _MAX_EXPLANATION
        )
        changes.append(f"- `{_inline_path(change.path)}` — {explanation}")
    body = (
        "### Summary\n\n"
        "This PR applies Buglensa's persisted proposed fix.\n\n"
        "### Changes\n\n"
        + "\n".join(changes)
        + "\n\n### Validation\n\n"
        + _validation_description(context)
        + f"\n\n{marker}"
    )
    return PullRequestDraft(
        title=title,
        body=_truncate_preserving_marker(body, marker),
        commit_message=commit_message,
        branch=branch,
        marker=marker,
    )


def _validation_description(context: PullRequestPublicationContext) -> str:
    validation = context.fix_validation
    if validation is None or validation.status in {"running", "not_run"}:
        return "Not validated — fix validation has not completed."
    if validation.status == "validated":
        return "Validated — available bounded checks passed."
    if validation.status == "blocked":
        return "Blocked — validation could not complete in the configured environment."
    if validation.status == "validation_failed":
        return "Validation failed — one or more available checks failed."
    return "Stale proposal — isolated validation found that the proposal baseline had changed."


async def _verify_existing_branch(
    installation_token: str,
    *,
    owner: str,
    repository: str,
    context: PullRequestPublicationContext,
    draft: PullRequestDraft,
    base_sha: str,
    base_entries: list[github_client.GitHubTreeEntry],
    branch_sha: str,
) -> None:
    branch_commit = await github_client.get_repository_git_commit(
        installation_token,
        owner=owner,
        repository=repository,
        commit_sha=branch_sha,
    )
    if (
        branch_commit.sha != branch_sha
        or branch_commit.parent_shas != (base_sha,)
        or branch_commit.message != draft.commit_message
    ):
        raise PullRequestConflictError(
            "The Buglensa pull request branch cannot be safely reconciled."
        )
    branch_entries = await github_client.get_repository_git_tree(
        installation_token,
        owner=owner,
        repository=repository,
        tree_sha=branch_commit.tree_sha,
    )
    base_tree = {
        entry.path: (entry.mode, entry.type, entry.sha)
        for entry in base_entries
        if entry.type != "tree"
    }
    branch_tree = {
        entry.path: (entry.mode, entry.type, entry.sha)
        for entry in branch_entries
        if entry.type != "tree"
    }
    changed_paths = {
        path
        for path in base_tree.keys() | branch_tree.keys()
        if base_tree.get(path) != branch_tree.get(path)
    }
    proposed_paths = {change.path for change in context.fix_proposal.files}
    if changed_paths != proposed_paths:
        raise PullRequestConflictError(
            "The Buglensa pull request branch contains unexpected changes."
        )
    for change in context.fix_proposal.files:
        base_entry = base_tree.get(change.path)
        branch_entry = branch_tree.get(change.path)
        if (
            base_entry is None
            or branch_entry is None
            or branch_entry[:2] != base_entry[:2]
            or branch_entry[1] != "blob"
        ):
            raise PullRequestConflictError(
                "The Buglensa pull request branch contains an unsafe change."
            )
        payload = await github_client.read_repository_file(
            installation_token,
            owner=owner,
            repository=repository,
            path=change.path,
            ref=branch_sha,
        )
        if _decode_repository_content(payload) != change.updated_content:
            raise PullRequestConflictError(
                "The Buglensa pull request branch does not match the proposal."
            )


def _verified_pull_request(
    created: github_client.GitHubCreatedPullRequest, marker: str
) -> github_client.GitHubCreatedPullRequest:
    if marker not in created.body:
        raise github_client.GitHubAPIError(
            "GitHub returned an inconsistent pull request."
        )
    return created


def _decode_repository_content(payload: dict) -> str:
    content = payload.get("content")
    if payload.get("type") != "file" or payload.get("encoding") != "base64" or not isinstance(content, str):
        raise PullRequestStaleError("A proposed file is no longer readable as text.")
    try:
        decoded = base64.b64decode("".join(content.split()), validate=True)
        if len(decoded) > MAX_FIX_CONTENT_BYTES:
            raise PullRequestStaleError("A proposed file exceeds the safe size limit.")
        return decoded.decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise PullRequestStaleError(
            "A proposed file is no longer readable as text."
        ) from exc


def _validate_change(path: str, original: str, updated: str) -> None:
    if is_forbidden_fix_path(path):
        raise PullRequestStaleError("A proposed path is not safe to publish.")
    if original == updated:
        raise PullRequestStaleError("A proposed file contains no change.")
    if len(original.encode("utf-8")) > MAX_FIX_CONTENT_BYTES or len(updated.encode("utf-8")) > MAX_FIX_CONTENT_BYTES:
        raise PullRequestStaleError("A proposed file exceeds the safe size limit.")


def _repository_parts(full_name: str) -> tuple[str, str]:
    parts = full_name.split("/")
    if len(parts) != 2 or not all(parts):
        raise github_client.GitHubAPIError("Repository identity is invalid.")
    return parts[0], parts[1]


def _clean(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = "".join(
        character
        for character in normalized
        if character in {"\n", "\t"}
        or not unicodedata.category(character).startswith("C")
    )
    return _MENTION.sub("@\u200b", cleaned).strip()


def _inline_path(value: str) -> str:
    return _clean(value).replace("`", "\\`").replace("\n", " ")


def _clean_inline(value: str) -> str:
    return (
        " ".join(_clean(value).split())
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _truncate(value: str, maximum: int) -> str:
    return value if len(value) <= maximum else value[:maximum].rstrip()


def _truncate_preserving_marker(body: str, marker: str) -> str:
    if len(body) <= _MAX_BODY:
        return body
    suffix = f"\n\n{marker}"
    return body[: _MAX_BODY - len(suffix)].rstrip() + suffix
