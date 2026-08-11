from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    github_app_id: str = ""
    github_app_slug: str = ""
    github_private_key: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""

    frontend_base_url: str = "http://localhost:3000"


@lru_cache
def get_settings() -> Settings:
    return Settings()
