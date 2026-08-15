import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.investigations import router as investigations_router
from app.integrations.github import router as github_router
from app.logging import configure_logging
from app.projects import router as projects_router

settings = get_settings()
configure_logging(level=settings.log_level, log_format=settings.log_format)

logger = structlog.get_logger(__name__)

app = FastAPI(title="BugLens API", servers=[{"url": settings.backend_base_url}])

# Signed, httponly browser session -- holds only the pending OAuth state and
# safe internal identifiers (user id, connection id), never credentials.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="buglens_session",
    same_site="lax",
    https_only=settings.session_cookie_secure,
)

# Added last so it's the outermost middleware and applies to every
# response, including ones from SessionMiddleware or error handling.
# allow_credentials=True is required so the browser will send/receive the
# session cookie on cross-origin requests from the Next.js frontend.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_base_url],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(github_router)
app.include_router(projects_router)
app.include_router(investigations_router)

logger.info("buglens_api_startup", log_format=settings.log_format)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
