"""SQLAlchemy models.

Kept separate from API routes and the GitHub integration package
(app/integrations/github/) -- this module only defines persisted schema,
nothing about HTTP or GitHub's API lives here.

GitHub OAuth access tokens are intentionally not stored on these models;
the OAuth callback discards the access token once it has used it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedAt, UpdatedAt, UUIDPrimaryKey


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUIDPrimaryKey]
    # BIGINT, not INTEGER: GitHub's numeric IDs are not guaranteed to stay
    # within int32 range, so this avoids ever needing to widen it later.
    github_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    github_login: Mapped[str]
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    connections: Mapped[list["GitHubConnection"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )


class GitHubInstallation(Base):
    """A GitHub App installation on some GitHub account/org.

    Not owned by a single BugLens user -- GitHubConnection is what links a
    User to the installation(s) they've connected, since the same
    installation could in principle be visible to more than one user.
    """

    __tablename__ = "github_installations"

    id: Mapped[UUIDPrimaryKey]
    github_installation_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, index=True
    )
    account_login: Mapped[str]
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    connections: Mapped[list["GitHubConnection"]] = relationship(
        back_populates="installation",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    projects: Mapped[list["Project"]] = relationship(
        back_populates="installation",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class GitHubConnection(Base):
    """Links a BugLens User to a GitHubInstallation they've connected.

    Modeled as its own association object (not a bare many-to-many table)
    so it has an id the browser session can reference directly, and its
    own timestamps.
    """

    __tablename__ = "github_connections"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "github_installation_id",
            name="uq_github_connections_user_installation",
        ),
    )

    id: Mapped[UUIDPrimaryKey]
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    # FK to the internal GitHubInstallation.id (UUID primary key) -- not
    # GitHub's own numeric installation id, which lives at
    # GitHubInstallation.github_installation_id.
    github_installation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("github_installations.id", ondelete="CASCADE")
    )
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    user: Mapped["User"] = relationship(back_populates="connections")
    installation: Mapped["GitHubInstallation"] = relationship(
        back_populates="connections"
    )


class Project(Base):
    """A BugLens project backed by a repository in an App installation."""

    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint(
            "github_installation_id",
            "github_repository_id",
            name="uq_projects_installation_repository",
        ),
    )

    id: Mapped[UUIDPrimaryKey]
    github_installation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("github_installations.id", ondelete="CASCADE")
    )
    name: Mapped[str]
    github_repository_id: Mapped[int] = mapped_column(BigInteger)
    github_repository_name: Mapped[str]
    github_repository_full_name: Mapped[str]
    default_branch: Mapped[str]
    app_url: Mapped[str | None]
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    installation: Mapped["GitHubInstallation"] = relationship(
        back_populates="projects"
    )
    investigations: Mapped[list["Investigation"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class InvestigationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Investigation(Base):
    """A persisted bug report belonging to a BugLens Project."""

    __tablename__ = "investigations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')",
            name="ck_investigations_status",
        ),
    )

    id: Mapped[UUIDPrimaryKey]
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str]
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        default=InvestigationStatus.PENDING.value,
        server_default=InvestigationStatus.PENDING.value,
    )
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    project: Mapped["Project"] = relationship(back_populates="investigations")
    evidence_items: Mapped[list["InvestigationEvidence"]] = relationship(
        back_populates="investigation",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    analysis: Mapped["InvestigationAnalysis | None"] = relationship(
        back_populates="investigation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )
    agent_run: Mapped["InvestigationAgentRun | None"] = relationship(
        back_populates="investigation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )


class EvidenceKind(StrEnum):
    RECORDING = "recording"
    LOGS = "logs"


class InvestigationEvidence(Base):
    """Metadata for recording or log evidence attached to an Investigation."""

    __tablename__ = "investigation_evidence"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('recording', 'logs')",
            name="ck_investigation_evidence_kind",
        ),
    )

    id: Mapped[UUIDPrimaryKey]
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str]
    mime_type: Mapped[str | None]
    filename: Mapped[str | None]
    storage_key: Mapped[str | None]
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    text_content: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    investigation: Mapped["Investigation"] = relationship(
        back_populates="evidence_items"
    )


class AnalysisConfidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class InvestigationAnalysis(Base):
    """Current structured Gemini understanding of an Investigation."""

    __tablename__ = "investigation_analyses"
    __table_args__ = (
        CheckConstraint(
            "confidence IN ('low', 'medium', 'high')",
            name="ck_investigation_analyses_confidence",
        ),
        UniqueConstraint(
            "investigation_id",
            name="uq_investigation_analyses_investigation_id",
        ),
    )

    id: Mapped[UUIDPrimaryKey]
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE")
    )
    model_name: Mapped[str]
    summary: Mapped[str] = mapped_column(Text)
    observed_behavior: Mapped[str] = mapped_column(Text)
    expected_behavior: Mapped[str | None] = mapped_column(Text)
    reproduction_steps: Mapped[list[str]] = mapped_column(JSONB)
    error_signals: Mapped[list[str]] = mapped_column(JSONB)
    suspected_components: Mapped[list[str]] = mapped_column(JSONB)
    confidence: Mapped[str]
    needs_more_information: Mapped[bool] = mapped_column(Boolean)
    missing_information: Mapped[list[str]] = mapped_column(JSONB)
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    investigation: Mapped["Investigation"] = relationship(back_populates="analysis")


class AgentRunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ReproductionStatus(StrEnum):
    REPRODUCED = "reproduced"
    NOT_REPRODUCED = "not_reproduced"
    BLOCKED = "blocked"


class GitHubIssuePublicationStatus(StrEnum):
    CREATING = "creating"
    CREATED = "created"
    FAILED = "failed"


class AgentRunProgressStage(StrEnum):
    STARTING = "starting"
    INVESTIGATING_REPOSITORY = "investigating_repository"
    SEARCHING_DUPLICATES = "searching_duplicates"
    PREPARING_REPRODUCTION = "preparing_reproduction"
    RUNNING_BROWSER = "running_browser"
    COMPLETED = "completed"
    FAILED = "failed"


class InvestigationAgentRun(Base):
    """One current autonomous repository/browser investigation result."""

    __tablename__ = "investigation_agent_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_investigation_agent_runs_status",
        ),
        CheckConstraint(
            "reproduction_status IS NULL OR reproduction_status IN "
            "('reproduced', 'not_reproduced', 'blocked')",
            name="ck_investigation_agent_runs_reproduction_status",
        ),
        CheckConstraint(
            "github_issue_status IS NULL OR github_issue_status IN "
            "('creating', 'created', 'failed')",
            name="ck_investigation_agent_runs_github_issue_status",
        ),
        CheckConstraint(
            "progress_stage IS NULL OR progress_stage IN "
            "('starting', 'investigating_repository', 'searching_duplicates', "
            "'preparing_reproduction', 'running_browser', 'completed', 'failed')",
            name="ck_investigation_agent_runs_progress_stage",
        ),
        UniqueConstraint(
            "investigation_id",
            name="uq_investigation_agent_runs_investigation_id",
        ),
    )

    id: Mapped[UUIDPrimaryKey]
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE")
    )
    status: Mapped[str]
    agent_model: Mapped[str]
    repository_summary: Mapped[list[dict] | None] = mapped_column(JSONB)
    duplicate_candidates: Mapped[list[dict]] = mapped_column(JSONB)
    reproduction_plan: Mapped[dict | None] = mapped_column(JSONB)
    generated_test: Mapped[str | None] = mapped_column(Text)
    reproduction_status: Mapped[str | None]
    execution_result: Mapped[dict | None] = mapped_column(JSONB)
    execution_summary: Mapped[str | None] = mapped_column(Text)
    execution_error: Mapped[str | None] = mapped_column(Text)
    github_issue_status: Mapped[str | None]
    github_issue_number: Mapped[int | None] = mapped_column(BigInteger)
    github_issue_title: Mapped[str | None]
    github_issue_url: Mapped[str | None]
    github_issue_created_at: Mapped[datetime | None]
    github_issue_publish_started_at: Mapped[datetime | None]
    progress_stage: Mapped[str | None]
    progress_message: Mapped[str | None] = mapped_column(Text)
    progress_updated_at: Mapped[datetime | None]
    run_attempt_id: Mapped[uuid.UUID | None]
    started_at: Mapped[datetime]
    completed_at: Mapped[datetime | None]
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    investigation: Mapped["Investigation"] = relationship(back_populates="agent_run")
