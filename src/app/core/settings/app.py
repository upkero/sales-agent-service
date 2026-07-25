from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """HTTP-surface settings. This service owns no database, so there is no
    DB_URL here — conversation state lives in-process (see the container)."""

    cors_allow_origins: list[str] = Field(
        default=["*"],
        description="Allowed CORS origins. Restrict in production.",
    )
    inbound_api_key: SecretStr | None = Field(
        default=None,
        description=(
            "If set, the turn endpoint requires this key in the X-API-Key header. "
            "Left unset the endpoint is open — convenient for a local demo, but set "
            "it before exposing the service, since every turn costs an LLM call."
        ),
    )
    turn_rate_limit_per_minute: int = Field(
        default=30,
        gt=0,
        description="Per-IP cap on POST /sales-agent/turn. Bounds both abuse and LLM spend.",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_app_settings() -> AppSettings:
    return AppSettings()
