"""Short-lived GitHub App access for an installation's repositories.

Key material, App JWTs, and installation tokens are deliberately kept in
local variables only and are never returned to callers or persisted.
"""

from __future__ import annotations

from app.config import Settings

from . import client as github_client
from .keys import resolve_private_key


async def load_installation_repositories(
    *, settings: Settings, github_installation_id: int
) -> list[github_client.GitHubRepository]:
    private_key = resolve_private_key(
        private_key_path=settings.github_private_key_path,
        private_key=settings.github_private_key,
    )
    try:
        app_jwt = github_client.create_app_jwt(
            client_id=settings.github_client_id,
            private_key=private_key,
        )
    finally:
        del private_key

    try:
        installation_token = await github_client.create_installation_access_token(
            installation_id=github_installation_id,
            app_jwt=app_jwt,
        )
    finally:
        del app_jwt

    try:
        return await github_client.list_installation_repositories(
            installation_token
        )
    finally:
        del installation_token
