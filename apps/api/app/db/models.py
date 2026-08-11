"""SQLAlchemy models.

Kept separate from API routes and the GitHub integration package
(app/integrations/github/) -- this module only defines persisted schema,
nothing about HTTP or GitHub's API lives here.

GitHub OAuth access tokens are intentionally not stored on these models
yet; the OAuth flow still uses its own temporary in-memory state.
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, ForeignKey
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

    installations: Mapped[list["GitHubInstallation"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )


class GitHubInstallation(Base):
    __tablename__ = "github_installations"

    id: Mapped[UUIDPrimaryKey]
    # ON DELETE CASCADE mirrors the ORM's ownership relationship (an
    # installation cannot outlive its user) at the database level, and
    # passive_deletes=True on the relationship above lets the DB do that
    # work instead of SQLAlchemy issuing per-row DELETEs.
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    github_installation_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, index=True
    )
    account_login: Mapped[str]
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    user: Mapped["User"] = relationship(back_populates="installations")
