import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.integrations.github import router as github_router
from app.logging import configure_logging

settings = get_settings()
configure_logging(level=settings.log_level, log_format=settings.log_format)

logger = structlog.get_logger(__name__)

app = FastAPI(title="BugLens API", servers=[{"url": settings.backend_base_url}])

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_base_url],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(github_router)

logger.info("buglens_api_startup", log_format=settings.log_format)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
