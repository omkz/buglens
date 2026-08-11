"""Database engine, session factory, and FastAPI session dependency.

Runtime application DB access is async (create_async_engine + AsyncSession)
since the GitHub OAuth callback and the rest of the API are async. Alembic
migrations stay synchronous on purpose -- see alembic/env.py -- there's no
need to run the migration tooling itself through an event loop.

Schema changes are managed exclusively through Alembic migrations (see
apps/api/alembic/) -- nothing here ever calls Base.metadata.create_all().
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

settings = get_settings()

# postgresql+psycopg selects psycopg 3's async implementation automatically
# under create_async_engine -- no URL or driver change needed vs. the sync
# engine Alembic uses.
engine = create_async_engine(settings.database_url, pool_pre_ping=True)

SessionLocal = async_sessionmaker(
    bind=engine, autoflush=False, expire_on_commit=False
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a request-scoped async session."""
    async with SessionLocal() as db:
        yield db
