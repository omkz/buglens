from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.integrations.github import router as github_router

settings = get_settings()

app = FastAPI(title="BugLens API", servers=[{"url": settings.backend_base_url}])

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_base_url],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(github_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
