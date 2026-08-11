"""SQLAlchemy models.

Kept separate from API routes and the GitHub integration package
(app/integrations/github/) -- this module only defines persisted schema,
nothing about HTTP or GitHub's API lives here.

GitHub OAuth access tokens are intentionally not stored on these models
yet; the OAuth flow still uses its own temporary in-memory state.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedAt, UpdatedAt, UUIDPrimaryKey


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUIDPrimaryKey]
    github_user_id: Mapped[int] = mapped_column(unique=True, index=True)
    github_login: Mapped[str]
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    installations: Mapped[list["GitHubInstallation"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class GitHubInstallation(Base):
    __tablename__ = "github_installations"

    id: Mapped[UUIDPrimaryKey]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    github_installation_id: Mapped[int] = mapped_column(unique=True, index=True)
    account_login: Mapped[str]
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    user: Mapped["User"] = relationship(back_populates="installations")
