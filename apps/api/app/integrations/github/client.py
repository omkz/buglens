"""Thin GitHub API client for the GitHub App OAuth connection flow.

Pure HTTP calls only — no FastAPI or storage concerns live here, so this
module stays easy to test and reason about independently of the routes.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API_BASE_URL = "https://api.github.com"

_REQUEST_TIMEOUT = 10.0


class GitHubOAuthError(RuntimeError):
    """Raised when a GitHub OAuth/API call fails or returns an unexpected result."""


@dataclass
class GitHubUser:
    id: int
    login: str


@dataclass
class GitHubInstallation:
    id: int
    app_id: int
    account_login: str | None


def build_authorize_url(*, client_id: str, redirect_uri: str, state: str) -> str:
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    return f"{GITHUB_AUTHORIZE_URL}?{query}"


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


def _auth_headers(access_token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {access_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
