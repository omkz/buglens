"""Authenticated API routes for persisted Buglensa projects."""

from __future__ import annotations

import uuid
from datetime import datetime

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, HttpUrl
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.session import get_db
from app.integrations.github import client as github_client
from app.integrations.github.access import load_installation_repositories
from app.integrations.github.repository import (
    PersistedGitHubConnection,
    get_connection_by_id,
)

from .repository import (
    DuplicateProjectError,
    PersistedProject,
    create_project as persist_project,
    list_projects as load_projects,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/projects", tags=["projects"])

_SESSION_CONNECTION_ID_KEY = "github_connection_id"


class CreateProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=255)
    github_repository_id: int = Field(gt=0)
    app_url: HttpUrl | None = None


class ProjectResponse(BaseModel):
    id: uuid.UUID
    name: str
    github_repository_id: int
    github_repository_full_name: str
    default_branch: str
    app_url: str | None
    created_at: datetime


class ProjectListResponse(BaseModel):
    projects: list[ProjectResponse]


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: CreateProjectRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
) -> PersistedProject:
    connection = await _require_connection(request, db)

    try:
        repositories = await load_installation_repositories(
            settings=settings,
            github_installation_id=connection.github_installation_id,
        )
    except (httpx.HTTPError, github_client.GitHubAPIError):
        logger.exception(
            "project_repository_validation_failed",
            installation_id=connection.github_installation_id,
            repository_id=payload.github_repository_id,
        )
        raise HTTPException(
            status_code=502,
            detail="Unable to validate the GitHub repository.",
        ) from None

    repository = next(
        (
            repository
            for repository in repositories
            if repository.id == payload.github_repository_id
        ),
        None,
    )
    if repository is None:
        raise HTTPException(
            status_code=404,
            detail="The selected GitHub repository is not accessible.",
        )

    try:
        project = await persist_project(
            db,
            installation_id=connection.installation_id,
            name=payload.name,
            github_repository_id=repository.id,
            github_repository_name=repository.name,
            github_repository_full_name=repository.full_name,
            default_branch=repository.default_branch,
            app_url=str(payload.app_url) if payload.app_url is not None else None,
        )
        await db.commit()
    except DuplicateProjectError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A project already exists for this GitHub repository.",
        ) from None
    except SQLAlchemyError:
        await db.rollback()
        logger.exception(
            "project_persist_failed",
            installation_id=connection.github_installation_id,
            repository_id=repository.id,
        )
        raise HTTPException(
            status_code=503,
            detail="Projects are temporarily unavailable.",
        ) from None

    logger.info(
        "project_created",
        project_id=str(project.id),
        installation_id=connection.github_installation_id,
        repository_id=repository.id,
    )
    return project


@router.get("", response_model=ProjectListResponse)
async def get_projects(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ProjectListResponse:
    connection = await _require_connection(request, db)
    try:
        projects = await load_projects(db, installation_id=connection.installation_id)
    except SQLAlchemyError:
        logger.exception(
            "projects_list_failed",
            installation_id=connection.github_installation_id,
        )
        raise HTTPException(
            status_code=503,
            detail="Projects are temporarily unavailable.",
        ) from None
    return ProjectListResponse(
        projects=[
            ProjectResponse.model_validate(project, from_attributes=True)
            for project in projects
        ]
    )


async def _require_connection(
    request: Request, db: AsyncSession
) -> PersistedGitHubConnection:
    raw_connection_id = request.session.get(_SESSION_CONNECTION_ID_KEY)
    if not raw_connection_id:
        raise HTTPException(status_code=401, detail="GitHub is not connected.")
    try:
        connection_id = uuid.UUID(raw_connection_id)
    except (AttributeError, TypeError, ValueError):
        raise HTTPException(
            status_code=401, detail="GitHub is not connected."
        ) from None

    try:
        connection = await get_connection_by_id(db, connection_id=connection_id)
    except SQLAlchemyError:
        logger.exception("projects_connection_db_failed")
        raise HTTPException(
            status_code=503,
            detail="Projects are temporarily unavailable.",
        ) from None
    if connection is None:
        raise HTTPException(status_code=401, detail="GitHub is not connected.")
    return connection
