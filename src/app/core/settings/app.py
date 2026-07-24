from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """HTTP-surface settings. This service owns no database, so there is no
    DB_URL here — conversation state lives in-process (see the container)."""

    cors_allow_origins: list[str] = Field(
        default=["*"],
        description="Allowed CORS origins. Restrict in production.",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_app_settings() -> AppSettings:
    return AppSettings()
