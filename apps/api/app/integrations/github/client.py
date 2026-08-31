"""Thin GitHub API client for OAuth and GitHub App installation access.

Pure HTTP calls only — no FastAPI or storage concerns live here, so this
module stays easy to test and reason about independently of the routes.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

import httpx
import jwt

GITHUB_INSTALL_BASE_URL = "https://github.com/apps"
GITHUB_OAUTH_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API_BASE_URL = "https://api.github.com"

_REQUEST_TIMEOUT = 10.0
_MAX_PULL_REQUEST_BODY_CHARACTERS = 100_000


class GitHubOAuthError(RuntimeError):
    """Raised when a GitHub OAuth/API call fails or returns an unexpected result."""


class GitHubAPIError(RuntimeError):
    """Raised when GitHub App authentication or API data is invalid."""


@dataclass
class GitHubUser:
    id: int
    login: str


@dataclass
class GitHubInstallation:
    id: int
    app_id: int
    account_login: str | None


@dataclass
class GitHubRepository:
    id: int
    name: str
    full_name: str
    private: bool
    default_branch: str
    html_url: str


@dataclass(frozen=True)
class GitHubRepositoryFile:
    path: str
    size: int


@dataclass(frozen=True)
class GitHubIssue:
    number: int
    title: str
    state: str
    html_url: str
    body_excerpt: str
    labels: list[str]


@dataclass(frozen=True)
class GitHubCreatedIssue:
    number: int
    title: str
    html_url: str
    state: str


@dataclass(frozen=True)
class GitHubGitCommit:
    sha: str
    tree_sha: str
    message: str = ""
    parent_shas: tuple[str, ...] = ()


@dataclass(frozen=True)
class GitHubTreeEntry:
    path: str
    mode: str
    type: str
    sha: str


@dataclass(frozen=True)
class GitHubCreatedPullRequest:
    number: int
    title: str
    html_url: str
    head_branch: str
    base_branch: str
    body: str


def build_install_url(*, app_slug: str, state: str) -> str:
    """Build the GitHub App installation URL.

    This assumes the App has "Request user authorization (OAuth) during
    installation" enabled, so GitHub will continue to the App's configured
    Setup URL (GITHUB_CALLBACK_URL) with `code`, `installation_id`,
    `setup_action`, and `state` once the user finishes installing.
    """
    query = urlencode({"state": state})
    return f"{GITHUB_INSTALL_BASE_URL}/{app_slug}/installations/new?{query}"


def build_user_authorization_url(
    *, client_id: str, redirect_uri: str, state: str
) -> str:
    """Build a GitHub user OAuth URL without requesting additional scopes."""
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    return f"{GITHUB_OAUTH_AUTHORIZE_URL}?{query}"


async def exchange_code_for_token(
    *, client_id: str, client_secret: str, code: str, redirect_uri: str
) -> str:
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as http_client:
        response = await http_client.post(
            GITHUB_TOKEN_URL,
            headers={"Accept": "application/json"},
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
    response.raise_for_status()
    payload = response.json()

    if "error" in payload:
        raise GitHubOAuthError(payload.get("error_description", payload["error"]))

    access_token = payload.get("access_token")
    if not access_token:
        raise GitHubOAuthError("GitHub did not return an access token.")

    return access_token


async def fetch_authenticated_user(access_token: str) -> GitHubUser:
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as http_client:
        response = await http_client.get(
            f"{GITHUB_API_BASE_URL}/user",
            headers=_auth_headers(access_token),
        )
    response.raise_for_status()
    payload = response.json()
    return GitHubUser(id=payload["id"], login=payload["login"])


async def fetch_user_installations(access_token: str) -> list[GitHubInstallation]:
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as http_client:
        response = await http_client.get(
            f"{GITHUB_API_BASE_URL}/user/installations",
            headers=_auth_headers(access_token),
        )
    response.raise_for_status()
    payload = response.json()

    return [
        GitHubInstallation(
            id=item["id"],
            app_id=item["app_id"],
            account_login=(item.get("account") or {}).get("login"),
        )
        for item in payload.get("installations", [])
    ]


def create_app_jwt(*, client_id: str, private_key: str) -> str:
    """Create a short-lived GitHub App JWT without retaining key material."""
    if not client_id or not private_key:
        raise GitHubAPIError("GitHub App credentials are not configured.")

    now = int(time.time())
    claims = {
        "iss": client_id,
        "iat": now - 60,
        "exp": now + 600,
    }
    normalized_key = private_key.replace("\\n", "\n")
    try:
        return jwt.encode(claims, normalized_key, algorithm="RS256")
    except (jwt.PyJWTError, TypeError, ValueError) as exc:
        raise GitHubAPIError("Unable to authenticate the GitHub App.") from exc


async def create_installation_access_token(
    *, installation_id: int, app_jwt: str
) -> str:
    async with _make_http_client() as http_client:
        response = await http_client.post(
            f"{GITHUB_API_BASE_URL}/app/installations/{installation_id}/access_tokens",
            headers=_auth_headers(app_jwt),
        )
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        raise GitHubAPIError(
            "GitHub returned an invalid installation token response."
        ) from exc
    access_token = payload.get("token") if isinstance(payload, dict) else None
    if not isinstance(access_token, str) or not access_token:
        raise GitHubAPIError("GitHub did not return an installation access token.")
    return access_token


async def list_installation_repositories(
    installation_access_token: str,
) -> list[GitHubRepository]:
    repositories: list[GitHubRepository] = []
    page = 1

    async with _make_http_client() as http_client:
        while True:
            response = await http_client.get(
                f"{GITHUB_API_BASE_URL}/installation/repositories",
                headers=_auth_headers(installation_access_token),
                params={"per_page": 100, "page": page},
            )
            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError as exc:
                raise GitHubAPIError(
                    "GitHub returned an invalid repository response."
                ) from exc
            items = payload.get("repositories") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                raise GitHubAPIError("GitHub returned an invalid repository response.")

            try:
                repositories.extend(
                    GitHubRepository(
                        id=int(item["id"]),
                        name=item["name"],
                        full_name=item["full_name"],
                        private=item["private"],
                        default_branch=item["default_branch"],
                        html_url=item["html_url"],
                    )
                    for item in items
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise GitHubAPIError(
                    "GitHub returned an invalid repository response."
                ) from exc

            if len(items) < 100:
                break
            page += 1

    return repositories


async def list_repository_tree(
    installation_access_token: str,
    *,
    owner: str,
    repository: str,
    ref: str,
) -> list[GitHubRepositoryFile]:
    """Return the recursive Git tree for one fixed repository and ref."""
    repository_url = (
        f"{GITHUB_API_BASE_URL}/repos/{quote(owner, safe='')}/"
        f"{quote(repository, safe='')}/git/trees/{quote(ref, safe='')}"
    )
    async with _make_http_client() as http_client:
        response = await http_client.get(
            repository_url,
            headers=_auth_headers(installation_access_token),
            params={"recursive": "1"},
        )
    response.raise_for_status()
    payload = _json_object(response, "repository tree")
    tree = payload.get("tree")
    if not isinstance(tree, list):
        raise GitHubAPIError("GitHub returned an invalid repository tree response.")

    files: list[GitHubRepositoryFile] = []
    try:
        for item in tree:
            if item.get("type") != "blob":
                continue
            files.append(
                GitHubRepositoryFile(path=item["path"], size=int(item.get("size", 0)))
            )
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise GitHubAPIError(
            "GitHub returned an invalid repository tree response."
        ) from exc
    return files


async def read_repository_file(
    installation_access_token: str,
    *,
    owner: str,
    repository: str,
    path: str,
    ref: str,
) -> dict[str, Any]:
    """Fetch one repository content object without decoding it."""
    content_url = (
        f"{GITHUB_API_BASE_URL}/repos/{quote(owner, safe='')}/"
        f"{quote(repository, safe='')}/contents/{quote(path, safe='/')}"
    )
    async with _make_http_client() as http_client:
        response = await http_client.get(
            content_url,
            headers=_auth_headers(installation_access_token),
            params={"ref": ref},
        )
    response.raise_for_status()
    return _json_object(response, "repository file")


async def search_repository_issues(
    installation_access_token: str,
    *,
    owner: str,
    repository: str,
    query: str,
    limit: int = 10,
) -> list[GitHubIssue]:
    """Search issues in one fixed repository; pull requests are excluded."""
    bounded_limit = max(1, min(limit, 10))
    terms = [
        term
        for term in re.findall(r"[A-Za-z0-9_.-]{2,64}", query[:500])
        if term.upper() not in {"AND", "OR", "NOT"}
    ][:20]
    if not terms:
        raise GitHubAPIError("Issue search query has no usable terms.")
    qualified_query = f"{' '.join(terms)} repo:{owner}/{repository} is:issue"
    async with _make_http_client() as http_client:
        response = await http_client.get(
            f"{GITHUB_API_BASE_URL}/search/issues",
            headers=_auth_headers(installation_access_token),
            params={"q": qualified_query, "per_page": bounded_limit},
        )
    response.raise_for_status()
    payload = _json_object(response, "issue search")
    items = payload.get("items")
    if not isinstance(items, list):
        raise GitHubAPIError("GitHub returned an invalid issue search response.")

    issues: list[GitHubIssue] = []
    try:
        for item in items[:bounded_limit]:
            labels = [
                label.get("name", "")
                for label in item.get("labels", [])
                if isinstance(label, dict) and isinstance(label.get("name"), str)
            ]
            body = item.get("body") or ""
            issues.append(
                GitHubIssue(
                    number=int(item["number"]),
                    title=str(item["title"])[:500],
                    state=str(item["state"]),
                    html_url=str(item["html_url"]),
                    body_excerpt=str(body)[:2_000],
                    labels=labels[:20],
                )
            )
    except (KeyError, TypeError, ValueError) as exc:
        raise GitHubAPIError("GitHub returned an invalid issue search response.") from exc
    return issues


async def find_repository_issue_by_marker(
    installation_access_token: str,
    *,
    owner: str,
    repository: str,
    marker: str,
) -> GitHubCreatedIssue | None:
    """Inspect a bounded set of recent issues for one exact hidden marker."""
    issues_url = (
        f"{GITHUB_API_BASE_URL}/repos/{quote(owner, safe='')}/"
        f"{quote(repository, safe='')}/issues"
    )
    async with _make_http_client() as http_client:
        response = await http_client.get(
            issues_url,
            headers=_auth_headers(installation_access_token),
            params={
                "state": "all",
                "sort": "created",
                "direction": "desc",
                "per_page": 20,
            },
        )
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        raise GitHubAPIError("GitHub returned an invalid issue list response.") from exc
    if not isinstance(payload, list):
        raise GitHubAPIError("GitHub returned an invalid issue list response.")
    for item in payload[:20]:
        if not isinstance(item, dict) or "pull_request" in item:
            continue
        body = item.get("body")
        if isinstance(body, str) and marker in body:
            return _created_issue_from_payload(
                item, owner=owner, repository=repository
            )
    return None


async def create_repository_issue(
    installation_access_token: str,
    *,
    owner: str,
    repository: str,
    title: str,
    body: str,
) -> GitHubCreatedIssue:
    """Create one issue in a fixed installation-scoped repository."""
    issues_url = (
        f"{GITHUB_API_BASE_URL}/repos/{quote(owner, safe='')}/"
        f"{quote(repository, safe='')}/issues"
    )
    async with _make_http_client() as http_client:
        response = await http_client.post(
            issues_url,
            headers=_auth_headers(installation_access_token),
            json={"title": title, "body": body},
        )
    response.raise_for_status()
    return _created_issue_from_payload(
        _json_object(response, "created issue"),
        owner=owner,
        repository=repository,
    )


async def get_repository_branch_sha(
    installation_access_token: str,
    *,
    owner: str,
    repository: str,
    branch: str,
) -> str:
    """Resolve one repository branch without accepting an arbitrary ref kind."""
    response = await _get_git_reference_response(
        installation_access_token,
        owner=owner,
        repository=repository,
        branch=branch,
    )
    response.raise_for_status()
    return _git_reference_sha(_json_object(response, "Git reference"))


async def find_repository_branch_sha(
    installation_access_token: str,
    *,
    owner: str,
    repository: str,
    branch: str,
) -> str | None:
    response = await _get_git_reference_response(
        installation_access_token,
        owner=owner,
        repository=repository,
        branch=branch,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return _git_reference_sha(_json_object(response, "Git reference"))


async def get_repository_git_commit(
    installation_access_token: str,
    *,
    owner: str,
    repository: str,
    commit_sha: str,
) -> GitHubGitCommit:
    url = _repository_api_url(owner, repository, f"git/commits/{quote(commit_sha, safe='')}")
    async with _make_http_client() as http_client:
        response = await http_client.get(url, headers=_auth_headers(installation_access_token))
    response.raise_for_status()
    payload = _json_object(response, "Git commit")
    sha = payload.get("sha")
    tree = payload.get("tree")
    tree_sha = tree.get("sha") if isinstance(tree, dict) else None
    message = payload.get("message")
    parents = payload.get("parents")
    if (
        not _is_git_sha(sha)
        or not _is_git_sha(tree_sha)
        or not isinstance(message, str)
        or not isinstance(parents, list)
    ):
        raise GitHubAPIError("GitHub returned an invalid Git commit response.")
    try:
        parent_shas = tuple(parent["sha"] for parent in parents)
    except (KeyError, TypeError) as exc:
        raise GitHubAPIError("GitHub returned an invalid Git commit response.") from exc
    if not all(_is_git_sha(parent_sha) for parent_sha in parent_shas):
        raise GitHubAPIError("GitHub returned an invalid Git commit response.")
    return GitHubGitCommit(
        sha=sha,
        tree_sha=tree_sha,
        message=message,
        parent_shas=parent_shas,
    )


async def get_repository_git_tree(
    installation_access_token: str,
    *,
    owner: str,
    repository: str,
    tree_sha: str,
) -> list[GitHubTreeEntry]:
    url = _repository_api_url(owner, repository, f"git/trees/{quote(tree_sha, safe='')}")
    async with _make_http_client() as http_client:
        response = await http_client.get(
            url,
            headers=_auth_headers(installation_access_token),
            params={"recursive": "1"},
        )
    response.raise_for_status()
    payload = _json_object(response, "Git tree")
    tree = payload.get("tree")
    if not isinstance(tree, list) or payload.get("truncated") is True:
        raise GitHubAPIError("GitHub returned an invalid Git tree response.")
    entries: list[GitHubTreeEntry] = []
    try:
        for item in tree:
            entry = GitHubTreeEntry(
                path=item["path"],
                mode=item["mode"],
                type=item["type"],
                sha=item["sha"],
            )
            if (
                not isinstance(entry.path, str)
                or not entry.path
                or len(entry.path) > 4_000
                or entry.mode not in {"100644", "100755", "120000", "040000", "160000"}
                or entry.type not in {"blob", "tree", "commit"}
                or not _is_git_sha(entry.sha)
            ):
                raise ValueError
            entries.append(entry)
    except (KeyError, TypeError, ValueError) as exc:
        raise GitHubAPIError("GitHub returned an invalid Git tree response.") from exc
    return entries


async def create_repository_blob(
    installation_access_token: str,
    *,
    owner: str,
    repository: str,
    content: str,
) -> str:
    return await _create_git_object(
        installation_access_token,
        owner=owner,
        repository=repository,
        resource="blobs",
        payload={"content": content, "encoding": "utf-8"},
        response_name="Git blob",
    )


async def create_repository_tree(
    installation_access_token: str,
    *,
    owner: str,
    repository: str,
    base_tree_sha: str,
    entries: list[GitHubTreeEntry],
) -> str:
    return await _create_git_object(
        installation_access_token,
        owner=owner,
        repository=repository,
        resource="trees",
        payload={
            "base_tree": base_tree_sha,
            "tree": [
                {
                    "path": entry.path,
                    "mode": entry.mode,
                    "type": entry.type,
                    "sha": entry.sha,
                }
                for entry in entries
            ],
        },
        response_name="Git tree",
    )


async def create_repository_commit(
    installation_access_token: str,
    *,
    owner: str,
    repository: str,
    message: str,
    tree_sha: str,
    parent_sha: str,
) -> str:
    return await _create_git_object(
        installation_access_token,
        owner=owner,
        repository=repository,
        resource="commits",
        payload={"message": message, "tree": tree_sha, "parents": [parent_sha]},
        response_name="Git commit",
    )


async def create_repository_branch(
    installation_access_token: str,
    *,
    owner: str,
    repository: str,
    branch: str,
    commit_sha: str,
) -> None:
    url = _repository_api_url(owner, repository, "git/refs")
    async with _make_http_client() as http_client:
        response = await http_client.post(
            url,
            headers=_auth_headers(installation_access_token),
            json={"ref": f"refs/heads/{branch}", "sha": commit_sha},
        )
    response.raise_for_status()
    payload = _json_object(response, "created Git reference")
    target = payload.get("object")
    target_sha = target.get("sha") if isinstance(target, dict) else None
    if (
        payload.get("ref") != f"refs/heads/{branch}"
        or target_sha != commit_sha
    ):
        raise GitHubAPIError("GitHub returned an invalid created Git reference response.")


async def find_repository_pull_request(
    installation_access_token: str,
    *,
    owner: str,
    repository: str,
    branch: str,
    base_branch: str,
    marker: str,
) -> GitHubCreatedPullRequest | None:
    url = _repository_api_url(owner, repository, "pulls")
    async with _make_http_client() as http_client:
        response = await http_client.get(
            url,
            headers=_auth_headers(installation_access_token),
            params={
                "state": "all",
                "head": f"{owner}:{branch}",
                "base": base_branch,
                "per_page": 20,
            },
        )
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        raise GitHubAPIError("GitHub returned an invalid pull request list response.") from exc
    if not isinstance(payload, list):
        raise GitHubAPIError("GitHub returned an invalid pull request list response.")
    for item in payload[:20]:
        if isinstance(item, dict) and isinstance(item.get("body"), str) and marker in item["body"]:
            return _created_pull_request_from_payload(
                item,
                owner=owner,
                repository=repository,
                expected_head=branch,
                expected_base=base_branch,
            )
    return None


async def create_repository_pull_request(
    installation_access_token: str,
    *,
    owner: str,
    repository: str,
    title: str,
    body: str,
    head_branch: str,
    base_branch: str,
) -> GitHubCreatedPullRequest:
    url = _repository_api_url(owner, repository, "pulls")
    async with _make_http_client() as http_client:
        response = await http_client.post(
            url,
            headers=_auth_headers(installation_access_token),
            json={
                "title": title,
                "body": body,
                "head": head_branch,
                "base": base_branch,
                "draft": False,
            },
        )
    response.raise_for_status()
    return _created_pull_request_from_payload(
        _json_object(response, "created pull request"),
        owner=owner,
        repository=repository,
        expected_head=head_branch,
        expected_base=base_branch,
    )


async def _get_git_reference_response(
    installation_access_token: str,
    *,
    owner: str,
    repository: str,
    branch: str,
) -> httpx.Response:
    url = _repository_api_url(owner, repository, f"git/ref/heads/{quote(branch, safe='')}")
    async with _make_http_client() as http_client:
        return await http_client.get(url, headers=_auth_headers(installation_access_token))


async def _create_git_object(
    installation_access_token: str,
    *,
    owner: str,
    repository: str,
    resource: str,
    payload: dict[str, Any],
    response_name: str,
) -> str:
    url = _repository_api_url(owner, repository, f"git/{resource}")
    async with _make_http_client() as http_client:
        response = await http_client.post(
            url,
            headers=_auth_headers(installation_access_token),
            json=payload,
        )
    response.raise_for_status()
    sha = _json_object(response, response_name).get("sha")
    if not _is_git_sha(sha):
        raise GitHubAPIError(f"GitHub returned an invalid {response_name} response.")
    return sha


def _repository_api_url(owner: str, repository: str, resource: str) -> str:
    return (
        f"{GITHUB_API_BASE_URL}/repos/{quote(owner, safe='')}/"
        f"{quote(repository, safe='')}/{resource}"
    )


def _git_reference_sha(payload: dict[str, Any]) -> str:
    target = payload.get("object")
    sha = target.get("sha") if isinstance(target, dict) else None
    if not _is_git_sha(sha):
        raise GitHubAPIError("GitHub returned an invalid Git reference response.")
    return sha


def _is_git_sha(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40,64}", value) is not None


def _make_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=_REQUEST_TIMEOUT)


def _auth_headers(access_token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {access_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _json_object(response: httpx.Response, resource: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise GitHubAPIError(f"GitHub returned an invalid {resource} response.") from exc
    if not isinstance(payload, dict):
        raise GitHubAPIError(f"GitHub returned an invalid {resource} response.")
    return payload


def _created_issue_from_payload(
    payload: dict[str, Any], *, owner: str, repository: str
) -> GitHubCreatedIssue:
    number = payload.get("number")
    title = payload.get("title")
    html_url = payload.get("html_url")
    state = payload.get("state")
    if (
        type(number) is not int
        or number <= 0
        or not isinstance(title, str)
        or not title.strip()
        or not isinstance(html_url, str)
        or state not in {"open", "closed"}
    ):
        raise GitHubAPIError("GitHub returned an invalid created issue response.")
    parsed_url = urlsplit(html_url)
    expected_path_prefix = f"/{owner}/{repository}/issues/"
    if (
        parsed_url.scheme != "https"
        or parsed_url.hostname != "github.com"
        or not parsed_url.path.startswith(expected_path_prefix)
    ):
        raise GitHubAPIError("GitHub returned an invalid created issue response.")
    return GitHubCreatedIssue(
        number=number,
        title=title[:500],
        html_url=html_url,
        state=state,
    )


def _created_pull_request_from_payload(
    payload: dict[str, Any],
    *,
    owner: str,
    repository: str,
    expected_head: str,
    expected_base: str,
) -> GitHubCreatedPullRequest:
    number = payload.get("number")
    title = payload.get("title")
    html_url = payload.get("html_url")
    body = payload.get("body")
    head = payload.get("head")
    base = payload.get("base")
    head_ref = head.get("ref") if isinstance(head, dict) else None
    base_ref = base.get("ref") if isinstance(base, dict) else None
    if (
        type(number) is not int
        or number <= 0
        or not isinstance(title, str)
        or not title.strip()
        or not isinstance(html_url, str)
        or not isinstance(body, str)
        or len(body) > _MAX_PULL_REQUEST_BODY_CHARACTERS
        or head_ref != expected_head
        or base_ref != expected_base
        or payload.get("draft") is not False
        or payload.get("state") not in {"open", "closed"}
    ):
        raise GitHubAPIError("GitHub returned an invalid pull request response.")
    parsed_url = urlsplit(html_url)
    expected_path_prefix = f"/{owner}/{repository}/pull/"
    if (
        parsed_url.scheme != "https"
        or parsed_url.hostname != "github.com"
        or not parsed_url.path.startswith(expected_path_prefix)
    ):
        raise GitHubAPIError("GitHub returned an invalid pull request response.")
    return GitHubCreatedPullRequest(
        number=number,
        title=title[:500],
        html_url=html_url,
        head_branch=head_ref,
        base_branch=base_ref,
        body=body,
    )
