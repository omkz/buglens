"""SQLAlchemy declarative base and shared column conventions.

Kept separate from app/db/session.py (engine/session wiring) and
app/db/models.py (actual tables) so each has one job: this module only
defines *how* columns are typed, not what the schema looks like or how to
connect to it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from sqlalchemy import DateTime, Uuid, func
from sqlalchemy.orm import DeclarativeBase, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {
        datetime: DateTime(timezone=True),
        uuid.UUID: Uuid(as_uuid=True),
    }


# Shared column conventions, reused across models for consistency.
# Primary keys are application-generated UUIDs (not DB sequences), so IDs
# exist before a row is flushed and don't leak row counts.
UUIDPrimaryKey = Annotated[
    uuid.UUID, mapped_column(primary_key=True, default=uuid.uuid4)
]
CreatedAt = Annotated[datetime, mapped_column(server_default=func.now())]
UpdatedAt = Annotated[
    datetime, mapped_column(server_default=func.now(), onupdate=func.now())
]
