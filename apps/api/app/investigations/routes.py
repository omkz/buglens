"""Authenticated APIs for creating and reading bug investigations."""

from __future__ import annotations

import uuid
from datetime import datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models
from app.db.session import get_db
from app.integrations.github.repository import (
    PersistedGitHubConnection,
    get_connection_by_id,
)

from .repository import (
    PersistedInvestigation,
    create_investigation as persist_investigation,
    get_investigation as load_investigation,
    list_investigations as load_investigations,
)

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["investigations"])

_SESSION_CONNECTION_ID_KEY = "github_connection_id"


class CreateInvestigationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10_000)


class InvestigationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    project_name: str
    github_repository_full_name: str
    title: str
    description: str | None
    status: models.InvestigationStatus
    created_at: datetime


class InvestigationListResponse(BaseModel):
    investigations: list[InvestigationResponse]


@router.post(
    "/projects/{project_id}/investigations",
    response_model=InvestigationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_investigation(
    project_id: uuid.UUID,
    payload: CreateInvestigationRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> PersistedInvestigation:
    connection = await _require_connection(request, db)
    try:
        investigation = await persist_investigation(
            db,
            installation_id=connection.installation_id,
            project_id=project_id,
            title=payload.title,
            description=payload.description or None,
        )
        if investigation is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        await db.commit()
    except HTTPException:
        raise
    except SQLAlchemyError:
        await db.rollback()
        logger.exception(
            "investigation_create_failed",
            project_id=str(project_id),
            installation_id=connection.github_installation_id,
        )
        raise HTTPException(
            status_code=503,
            detail="Investigations are temporarily unavailable.",
        ) from None

    logger.info(
        "investigation_created",
        investigation_id=str(investigation.id),
        project_id=str(project_id),
        installation_id=connection.github_installation_id,
    )
    return investigation


@router.get("/investigations", response_model=InvestigationListResponse)
async def get_investigations(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> InvestigationListResponse:
    connection = await _require_connection(request, db)
    try:
        investigations = await load_investigations(
            db, installation_id=connection.installation_id
        )
    except SQLAlchemyError:
        logger.exception(
            "investigations_list_failed",
            installation_id=connection.github_installation_id,
        )
        raise HTTPException(
            status_code=503,
            detail="Investigations are temporarily unavailable.",
        ) from None
    return InvestigationListResponse(
        investigations=[
            InvestigationResponse.model_validate(investigation)
            for investigation in investigations
        ]
    )


@router.get(
    "/investigations/{investigation_id}", response_model=InvestigationResponse
)
async def get_investigation(
    investigation_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> PersistedInvestigation:
    connection = await _require_connection(request, db)
    try:
        investigation = await load_investigation(
            db,
            installation_id=connection.installation_id,
            investigation_id=investigation_id,
        )
    except SQLAlchemyError:
        logger.exception(
            "investigation_detail_failed",
            investigation_id=str(investigation_id),
            installation_id=connection.github_installation_id,
        )
        raise HTTPException(
            status_code=503,
            detail="Investigations are temporarily unavailable.",
        ) from None
    if investigation is None:
        raise HTTPException(status_code=404, detail="Investigation not found.")
    return investigation


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
        logger.exception("investigations_connection_db_failed")
        raise HTTPException(
            status_code=503,
            detail="Investigations are temporarily unavailable.",
        ) from None
    if connection is None:
        raise HTTPException(status_code=401, detail="GitHub is not connected.")
    return connection
