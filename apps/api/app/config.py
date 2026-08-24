from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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
    backend_base_url: str = "http://localhost:8000"
    github_callback_url: str = "http://localhost:8000/github/oauth/callback"

    log_level: str = "INFO"
    log_format: str = "console"

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/buglens"

    evidence_storage_backend: Literal["local", "gcs"] = "local"
    evidence_storage_dir: Path = Path(".data/evidence")
    gcs_bucket: str = ""
    max_evidence_upload_bytes: int = Field(default=100 * 1024 * 1024, gt=0)

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"
    gemini_file_processing_timeout_seconds: float = Field(default=300, gt=0)

    playwright_action_timeout_ms: int = Field(default=10_000, gt=0, le=120_000)
    playwright_run_timeout_seconds: float = Field(default=60, gt=0, le=600)
    playwright_allow_private_network: bool = False

    session_secret: str
    session_cookie_secure: bool = False

    @model_validator(mode="after")
    def validate_evidence_storage(self) -> Self:
        if self.evidence_storage_backend == "gcs" and not self.gcs_bucket.strip():
            raise ValueError(
                "GCS_BUCKET is required when EVIDENCE_STORAGE_BACKEND=gcs."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
