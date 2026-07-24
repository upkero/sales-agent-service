from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LLMProvider = Literal["openai", "openai_compatible", "ollama"]


class LLMSettings(BaseSettings):
    provider: LLMProvider = Field(
        default="openai",
        description=(
            "LLM provider profile. Ollama and openai_compatible use the "
            "OpenAI-compatible client with a provider-specific base URL."
        ),
    )
    model: str = Field(
        ...,
        min_length=1,
        description="LLM model name sent to the provider.",
    )
    api_key: str | None = Field(
        default=None,
        description="Provider API key. Required for provider='openai'.",
    )
    base_url: str | None = Field(
        default=None,
        description="Optional OpenAI-compatible API base URL.",
    )
    timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        description="LLM client timeout in seconds.",
    )
    max_retries: int = Field(
        default=2,
        ge=0,
        description="Maximum provider SDK retries.",
    )
    temperature: float | None = Field(
        default=0.2,
        ge=0.0,
        le=2.0,
        description="Sampling temperature. A little warmth helps a sales reply read naturally.",
    )
    max_tokens: int | None = Field(
        default=None,
        gt=0,
        description="Optional maximum number of output tokens.",
    )
    reasoning_effort: str | None = Field(
        default=None,
        description="Reasoning effort level (o-series / xAI models).",
    )

    model_config = SettingsConfigDict(
        env_prefix="LLM_",
        env_file=".env",
        extra="ignore",
    )

    @model_validator(mode="after")
    def validate_provider_requirements(self) -> "LLMSettings":
        if self.provider == "openai" and not self.api_key:
            raise ValueError("api_key is required when provider='openai'.")
        if self.provider != "openai" and not self.base_url:
            raise ValueError("base_url is required when provider is openai_compatible or ollama.")
        return self


@lru_cache(maxsize=1)
def get_llm_settings() -> LLMSettings:
    return LLMSettings()
