"""FastAPI routes for the GitHub App "Connect GitHub" OAuth flow."""

from __future__ import annotations

import secrets
import uuid

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.session import get_db

from . import client as github_client
from .repository import get_connection_by_id, persist_github_connection
from .selection import InstallationSelectionError, select_installation

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/github", tags=["github"])

_SESSION_OAUTH_STATE_KEY = "github_oauth_state"
_SESSION_CONNECTION_ID_KEY = "github_connection_id"


@router.get("/install-url")
def get_install_url(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    if not settings.github_app_slug:
        raise HTTPException(
            status_code=503,
            detail="GitHub App is not configured on this server.",
        )

    state = secrets.token_urlsafe(24)
    request.session[_SESSION_OAUTH_STATE_KEY] = state

    url = github_client.build_install_url(
        app_slug=settings.github_app_slug,
        state=state,
    )
    return {"url": url}


@router.get("/status")
async def get_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool | int | str | None]:
    disconnected = {"connected": False, "installation_id": None, "account_login": None}

    raw_connection_id = request.session.get(_SESSION_CONNECTION_ID_KEY)
    if not raw_connection_id:
        return disconnected

    try:
        connection_id = uuid.UUID(raw_connection_id)
    except ValueError:
        return disconnected

    connection = await get_connection_by_id(db, connection_id=connection_id)
    if connection is None:
        # The session points at a connection that no longer exists (e.g.
        # deleted) -- treat as disconnected rather than guessing.
        return disconnected

    logger.info(
        "github_connection_restored",
        installation_id=connection.github_installation_id,
    )

    return {
        "connected": True,
        "installation_id": connection.github_installation_id,
        "account_login": connection.account_login,
    }


@router.get("/oauth/callback")
async def github_oauth_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    # GitHub also sends installation_id and setup_action here when "Request
    # user authorization (OAuth) during installation" is enabled. They are
    # accepted for logging only — never trusted for the connection decision.
    # See the verification against /user/installations below.
    installation_id: int | None = Query(default=None),
    setup_action: str | None = Query(default=None),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    redirect_target = f"{settings.frontend_base_url}/projects"

    logger.info(
        "github_oauth_callback_received",
        setup_action=setup_action,
        raw_installation_id=installation_id,
    )

    # Single-use: pop the expected state so it can never be replayed,
    # whether or not it ends up matching.
    expected_state = request.session.pop(_SESSION_OAUTH_STATE_KEY, None)
    if not expected_state or not state or expected_state != state:
        logger.warning("github_oauth_invalid_state")
        return RedirectResponse(f"{redirect_target}?github_error=invalid_state")

    if not code:
        logger.warning("github_oauth_missing_code")
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
        logger.exception("github_oauth_failed")
        return RedirectResponse(f"{redirect_target}?github_error=oauth_failed")

    # The access token is only needed for the three calls above. It is
    # never persisted or stored in the session, so drop the reference now
    # rather than letting it linger for the rest of the request.
    del access_token

    # Never trust a raw installation_id from a query parameter: only accept
    # an installation the authenticated user's own token can see, verified
    # to belong to our GitHub App.
    try:
        selected_installation = select_installation(
            installations,
            app_id=settings.github_app_id,
            requested_installation_id=installation_id,
        )
    except InstallationSelectionError as exc:
        logger.warning(
            "github_installation_selection_failed",
            github_username=user.login,
            error_code=exc.code,
        )
        return RedirectResponse(f"{redirect_target}?github_error={exc.code}")

    try:
        persisted = await persist_github_connection(
            db,
            github_user_id=user.id,
            github_login=user.login,
            github_installation_id=selected_installation.id,
            account_login=selected_installation.account_login or user.login,
        )
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        logger.exception(
            "github_connection_persist_failed",
            github_username=user.login,
        )
        return RedirectResponse(f"{redirect_target}?github_error=persist_failed")

    # Only safe internal identifiers go in the session -- never credentials.
    request.session[_SESSION_CONNECTION_ID_KEY] = str(persisted.connection_id)

    logger.info(
        "github_connection_persisted",
        github_username=user.login,
        installation_id=persisted.github_installation_id,
        setup_action=setup_action,
    )

    return RedirectResponse(redirect_target)
