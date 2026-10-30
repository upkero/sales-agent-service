"""The cost of a model call, as OpenRouter reports it, reaches the `llm.cost` counter.

Same contract as rag-chat-service: the flag that makes OpenRouter send `usage.cost` goes
to openrouter.ai only, and a provider that reports no cost records none.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.contracts.llm.llm_message import LLMMessage
from src.app.core.settings.llm import LLMSettings
from src.app.llm.openai_compatible_llm_client import OpenAICompatibleLLMClient
from src.app.llm.traced import traced_complete
from tests import telemetry

OPENROUTER = "https://openrouter.ai/api/v1"
MODEL = "openai/gpt-4o-mini"
_MESSAGES = [LLMMessage(role="user", content="hi")]


def _completion(*, cost: float | None = 0.0004) -> SimpleNamespace:
    usage = SimpleNamespace(prompt_tokens=120, completion_tokens=17, total_tokens=137)
    if cost is not None:
        usage.cost = cost
    choice = SimpleNamespace(message=SimpleNamespace(content="{}"), finish_reason="stop")
    return SimpleNamespace(id="gen-1", created=1, model=MODEL, choices=[choice], usage=usage)


def _llm(base_url: str, completion: object) -> tuple[OpenAICompatibleLLMClient, MagicMock]:
    sdk = MagicMock()
    sdk.chat.completions.create = AsyncMock(return_value=completion)
    settings = LLMSettings(_env_file=None, provider="openai_compatible", model=MODEL, base_url=base_url)
    return OpenAICompatibleLLMClient(settings=settings, client=sdk), sdk


async def test_openrouter_is_asked_for_the_exact_cost_and_it_is_recorded() -> None:
    llm, sdk = _llm(OPENROUTER, _completion())
    before = telemetry.reading("llm.cost", provider="openrouter", model=MODEL)

    await traced_complete(llm, _MESSAGES, purpose="reply")

    assert sdk.chat.completions.create.await_args.kwargs["extra_body"] == {"usage": {"include": True}}
    assert telemetry.reading("llm.cost", provider="openrouter", model=MODEL) - before == pytest.approx(0.0004)


async def test_another_endpoint_is_not_sent_openrouters_flag_and_records_no_cost() -> None:
    llm, sdk = _llm("http://host.docker.internal:11434/v1", _completion(cost=None))
    before = telemetry.reading("llm.cost")

    await traced_complete(llm, _MESSAGES, purpose="reply")

    assert "extra_body" not in sdk.chat.completions.create.await_args.kwargs
    assert telemetry.reading("llm.cost") == before


async def test_the_cost_is_on_the_model_call_span() -> None:
    llm, _ = _llm(OPENROUTER, _completion())
    telemetry.clear_spans()

    await traced_complete(llm, _MESSAGES, purpose="reply")

    (span,) = [s for s in telemetry.finished_spans() if s.name == f"chat {MODEL}"]
    assert span.attributes is not None
    assert span.attributes["llm.cost"] == pytest.approx(0.0004)
