"""FastAPI routes for the GitHub App "Connect GitHub" OAuth flow."""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse

from app.config import Settings, get_settings

from . import client as github_client
from .state import GitHubConnection, GitHubConnectionStore, get_connection_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/github", tags=["github"])


@router.get("/install-url")
def get_install_url(
    settings: Settings = Depends(get_settings),
    store: GitHubConnectionStore = Depends(get_connection_store),
) -> dict[str, str]:
    if not settings.github_app_slug:
        raise HTTPException(
            status_code=503,
            detail="GitHub App is not configured on this server.",
        )

    state = store.create_pending_state()
    url = github_client.build_install_url(
        app_slug=settings.github_app_slug,
        state=state,
    )
    return {"url": url}


@router.get("/status")
def get_status(
    store: GitHubConnectionStore = Depends(get_connection_store),
) -> dict[str, bool | int | str | None]:
    connection = store.get_connection()
    if connection is None:
        return {"connected": False, "installation_id": None, "account_login": None}

    return {
        "connected": True,
        "installation_id": connection.installation_id,
        "account_login": connection.account_login,
    }


@router.get("/oauth/callback")
async def github_oauth_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    # GitHub also sends installation_id and setup_action here when "Request
    # user authorization (OAuth) during installation" is enabled. They are
    # accepted for logging only — never trusted for the connection decision.
    # See the verification against /user/installations below.
    installation_id: int | None = Query(default=None),
    setup_action: str | None = Query(default=None),
    settings: Settings = Depends(get_settings),
    store: GitHubConnectionStore = Depends(get_connection_store),
) -> RedirectResponse:
    redirect_target = f"{settings.frontend_base_url}/projects"

    logger.info(
        "GitHub install callback received: setup_action=%s raw_installation_id=%s (unverified)",
        setup_action,
        installation_id,
    )

    if not store.consume_pending_state(state):
        logger.warning("Rejected GitHub OAuth callback with an invalid/expired state.")
        return RedirectResponse(f"{redirect_target}?github_error=invalid_state")

    if not code:
        logger.warning("GitHub OAuth callback is missing the 'code' parameter.")
        return RedirectResponse(f"{redirect_target}?github_error=missing_code")

    try:
        access_token = await github_client.exchange_code_for_token(
            client_id=settings.github_client_id,
            client_secret=settings.github_client_secret,
            code=code,
            redirect_uri=settings.github_callback_url,
        )
        user = await github_client.fetch_authenticated_user(access_token)
        installations = await github_client.fetch_user_installations(access_token)
    except (httpx.HTTPError, github_client.GitHubOAuthError):
        logger.exception("GitHub OAuth exchange failed.")
        return RedirectResponse(f"{redirect_target}?github_error=oauth_failed")

    # Never trust a raw installation_id from a query parameter: only accept
    # installations the authenticated user's own token can see, matched to
    # our GitHub App.
    matching_installation = next(
        (
            installation
            for installation in installations
            if str(installation.app_id) == settings.github_app_id
        ),
        None,
    )

    if matching_installation is None:
        logger.warning(
            "GitHub user %s authorized BugLens but has no installation of app %s.",
            user.login,
            settings.github_app_id,
        )
        return RedirectResponse(f"{redirect_target}?github_error=app_not_installed")

    store.save_connection(
        GitHubConnection(
            installation_id=matching_installation.id,
            account_login=user.login,
            access_token=access_token,
        )
    )

    return RedirectResponse(redirect_target)
