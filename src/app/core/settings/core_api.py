from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class CoreApiSettings(BaseSettings):
    """Where the price list lives.

    Every /api/v1 endpoint of ops-core-api requires X-API-Key, so the key is
    required here too. With a default of None the service would start happily
    and answer the first prospect with a 401 dressed up as "pricing is
    unavailable" — failing at startup says what is actually wrong.
    """

    base_url: str = Field(
        default="http://localhost:8000",
        description="ops-core-api root. In docker compose use http://host.docker.internal:8000.",
    )
    api_key: SecretStr = Field(
        ...,
        min_length=16,
        description="Shared secret for ops-core-api, sent as the X-API-Key header.",
    )
    timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        description="Per-request timeout. A prospect is waiting on a reply, so this stays small.",
    )
    max_attempts: int = Field(
        default=3,
        ge=1,
        le=5,
        description="Total attempts per call, including the first. Bounded: retries cost the prospect silence.",
    )

    model_config = SettingsConfigDict(env_prefix="CORE_API_", env_file=".env", extra="ignore")


@lru_cache(maxsize=1)
def get_core_api_settings() -> CoreApiSettings:
    return CoreApiSettings()
