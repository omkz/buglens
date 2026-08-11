"""GitHub App connection integration.

Covers the "Connect GitHub" onboarding step: building the GitHub App
installation URL, handling the installation callback, and tracking the
resulting installation for the current server process. There is no
database yet, so the connection state lives in memory for the lifetime of
the process (development/demo only).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

from app.config import get_settings

router = APIRouter(prefix="/github", tags=["github"])


@dataclass
class GitHubConnectionState:
    pending_state: str | None = None
    installation_id: int | None = None

    @property
    def is_connected(self) -> bool:
        return self.installation_id is not None


_connection_state = GitHubConnectionState()


@router.get("/install-url")
def get_install_url() -> dict[str, str]:
    settings = get_settings()
    if not settings.github_app_slug:
        raise HTTPException(
            status_code=503,
            detail="GitHub App is not configured on this server.",
        )

    state = secrets.token_urlsafe(24)
    _connection_state.pending_state = state

    query = urlencode({"state": state})
    url = f"https://github.com/apps/{settings.github_app_slug}/installations/new?{query}"
    return {"url": url}


@router.get("/status")
def get_status() -> dict[str, bool | int | None]:
    return {
        "connected": _connection_state.is_connected,
        "installation_id": _connection_state.installation_id,
    }


@router.get("/callback")
def github_callback(
    installation_id: int | None = Query(default=None),
    setup_action: str | None = Query(default=None),
    state: str | None = Query(default=None),
) -> RedirectResponse:
    if installation_id is not None and state == _connection_state.pending_state:
        _connection_state.pending_state = None
        _connection_state.installation_id = installation_id

    settings = get_settings()
    return RedirectResponse(f"{settings.frontend_base_url}/projects")
