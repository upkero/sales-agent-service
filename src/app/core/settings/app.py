from functools import lru_cache
from typing import Annotated, Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class AppSettings(BaseSettings):
    """HTTP-surface settings. This service owns no database, so there is no
    DB_URL here — conversation state lives in-process (see the container)."""

    # NoDecode stops the settings source from JSON-decoding this field, which it
    # does for any complex type before validators run. Without it a plain
    # "a,b" env value fails at parse time and split_comma_separated never sees it.
    #
    # Closed by default: an origin list is a deployment decision, and a service
    # that ships with "*" is one that never gets narrowed. A browser client
    # cannot reach this service until CORS_ALLOWED_ORIGINS names its origin.
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(
        default=[],
        description="Allowed CORS origins, comma-separated in the environment. Empty means no browser client.",
    )
    turn_rate_limit_per_minute: int = Field(
        default=30,
        gt=0,
        description="Per-IP cap on POST /api/v1/turn. Bounds both abuse and LLM spend.",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def split_comma_separated(cls, value: Any) -> Any:
        # pydantic-settings parses a bare list[str] env var as JSON, so a plain
        # "a,b" value would fail validation without this.
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache(maxsize=1)
def get_app_settings() -> AppSettings:
    return AppSettings()
