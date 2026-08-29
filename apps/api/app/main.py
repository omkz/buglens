from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.db.session import engine
from app.investigations import router as investigations_router
from app.integrations.github import router as github_router
from app.logging import configure_logging
from app.projects import router as projects_router

settings = get_settings()
configure_logging(level=settings.log_level, log_format=settings.log_format)

logger = structlog.get_logger(__name__)
API_PREFIX = "/api"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    try:
        yield
    finally:
        await engine.dispose()


app = FastAPI(
    title="Buglensa API",
    lifespan=lifespan,
)

# Signed, httponly browser session -- holds only the pending OAuth state and
# safe internal identifiers (user id, connection id), never credentials.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="buglens_session",
    same_site="lax",
    https_only=settings.session_cookie_secure,
)

app.include_router(github_router, prefix=API_PREFIX)
app.include_router(projects_router, prefix=API_PREFIX)
app.include_router(investigations_router, prefix=API_PREFIX)

logger.info("buglens_api_startup", log_format=settings.log_format)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
async def readiness(response: Response) -> dict[str, str]:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError) as exc:
        logger.warning(
            "buglens_api_readiness_failed",
            exception_type=type(exc).__name__,
        )
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable"}
    return {"status": "ready"}
