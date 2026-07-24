from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

AgentLanguage = Literal["en", "ru"]


class SalesAgentSettings(BaseSettings):
    """The persona and the guard rails of the selling agent.

    The persona strings are here rather than hard-coded in the stages so the
    same state machine can front a different product or brand by changing
    environment, not code.
    """

    name: str = Field(
        default="Alex",
        min_length=1,
        max_length=40,
        description="What the agent calls itself.",
    )
    company: str = Field(
        default="Aurora Wellness",
        min_length=1,
        max_length=80,
        description="The business the agent sells for. Its catalogue is priced by ops-core-api.",
    )
    language: AgentLanguage = Field(
        default="en",
        description="Conversation language. Drives the persona instructions.",
    )
    history_limit: int = Field(
        default=20,
        ge=2,
        le=100,
        description="How many past messages are replayed to the model each turn. Bounds prompt growth.",
    )
    max_parse_failures: int = Field(
        default=2,
        ge=1,
        le=5,
        description=(
            "Consecutive turns the model may return unparseable control JSON before the agent "
            "stops re-asking and escalates to a human hand-off. Bounded so a broken model can "
            "never wedge the conversation in an endless retry."
        ),
    )

    model_config = SettingsConfigDict(env_prefix="AGENT_", env_file=".env", extra="ignore")


@lru_cache(maxsize=1)
def get_agent_settings() -> SalesAgentSettings:
    return SalesAgentSettings()
