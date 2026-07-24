from openai import AsyncOpenAI

from src.app.core.settings.llm import LLMSettings
from src.app.llm.openai_compatible_llm_client import OpenAICompatibleLLMClient


def create_llm_client(settings: LLMSettings) -> OpenAICompatibleLLMClient:
    """Factory: the one place the raw AsyncOpenAI SDK client is instantiated.

    Keeping construction here means the rest of the app depends on the LLMClient
    port, never on the SDK — swapping providers is a settings change, and a test
    never has to build a real client.
    """
    raw_client = AsyncOpenAI(
        api_key=settings.api_key,
        base_url=settings.base_url,
        timeout=settings.timeout_seconds,
        max_retries=settings.max_retries,
    )
    return OpenAICompatibleLLMClient(settings=settings, client=raw_client)
