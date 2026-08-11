"""SQLAlchemy models.

Kept separate from API routes and the GitHub integration package
(app/integrations/github/) -- this module only defines persisted schema,
nothing about HTTP or GitHub's API lives here.

GitHub OAuth access tokens are intentionally not stored on these models;
the OAuth callback discards the access token once it has used it.
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, ForeignKey, UniqueConstraint
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
