from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConversationStoreSettings(BaseSettings):
    """Bounds on the in-memory conversation store.

    Both knobs exist to stop the process leaking memory: abandoned conversations
    must not live forever, and a burst of new ones must not grow the store without
    limit. See repositories/memory_conversation.py for how they are applied.
    """

    ttl_seconds: float = Field(
        default=3600.0,
        gt=0,
        description="How long a conversation survives without activity before it is evicted. Default 1 hour.",
    )
    max_entries: int = Field(
        default=10_000,
        ge=1,
        description="Hard cap on stored conversations. Beyond it, the least-recently-active one is dropped.",
    )

    model_config = SettingsConfigDict(env_prefix="CONVERSATION_", env_file=".env", extra="ignore")


@lru_cache(maxsize=1)
def get_conversation_store_settings() -> ConversationStoreSettings:
    return ConversationStoreSettings()
