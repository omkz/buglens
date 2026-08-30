from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/buglens"


class Settings(DatabaseSettings):
    github_app_id: str = ""
    github_app_slug: str = ""
    github_private_key: str = ""
    # Path to a PEM file, preferred over github_private_key when set -- lets
    # local development point at a real .pem file instead of escaping
    # newlines in .env. See app/integrations/github/keys.py.
    github_private_key_path: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""

    frontend_base_url: str = "http://localhost:3000"
    backend_base_url: str = "http://localhost:3000/api"
    github_callback_url: str = "http://localhost:3000/api/github/oauth/callback"

    log_level: str = "INFO"
    log_format: str = "console"

    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_max_overflow: int = Field(default=2, ge=0, le=50)
    database_pool_timeout_seconds: float = Field(default=30, gt=0, le=120)
    database_pool_recycle_seconds: int = Field(default=1800, ge=60)

    evidence_storage_backend: Literal["local", "gcs"] = "local"
    evidence_storage_dir: Path = Path(".data/evidence")
    gcs_bucket: str = ""
    max_evidence_upload_bytes: int = Field(default=100 * 1024 * 1024, gt=0)

    google_cloud_project: str = ""
    google_cloud_location: str = ""
    gemini_model: str = "gemini-3.6-flash"
    gemini_file_processing_timeout_seconds: float = Field(default=300, gt=0)
    agent_run_timeout_seconds: float = Field(default=180, gt=0, le=900)

    playwright_action_timeout_ms: int = Field(default=10_000, gt=0, le=120_000)
    playwright_run_timeout_seconds: float = Field(default=60, gt=0, le=600)
    playwright_allow_private_network: bool = False

    fix_validation_allow_host_execution: bool = False
    fix_validation_trusted_repositories: str = ""

    session_secret: str
    session_cookie_secure: bool = False

    @model_validator(mode="after")
    def validate_evidence_storage(self) -> Self:
        if self.evidence_storage_backend == "gcs" and not self.gcs_bucket.strip():
            raise ValueError(
                "GCS_BUCKET is required when EVIDENCE_STORAGE_BACKEND=gcs."
            )
        return self

    @property
    def trusted_fix_validation_repositories(self) -> frozenset[str]:
        return frozenset(
            repository.strip()
            for repository in self.fix_validation_trusted_repositories.split(",")
            if repository.strip()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_database_settings() -> DatabaseSettings:
    return DatabaseSettings()


def escape_alembic_url(database_url: str) -> str:
    """Escape ConfigParser interpolation markers in an Alembic URL."""
    return database_url.replace("%", "%%")
