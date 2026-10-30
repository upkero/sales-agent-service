"""One model call as a `chat {model}` span, a duration and a token count.

Wrapped around `LLMClient.complete` by the stages, not inside the client, so the
span is the same whichever client is behind the port and the port stays as it was.
`purpose` is a plain argument for the same reason: the stage that makes the call
is the only one who knows whether it is the reply, the re-ask or the upsell read.

Only metadata goes on the span. The messages and the reply are the customer's
words, and an error is recorded by its type, never its text.
"""

import time
from collections.abc import Sequence
from typing import Literal

from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from src.app.contracts.llm.llm_message import LLMMessage
from src.app.contracts.llm.llm_response import LLMResponse
from src.app.core.metrics import record_llm_call, record_llm_cost
from src.app.core.turn_context import current_turn_stats
from src.app.interfaces.llm.llm_client import LLMClient

LLMPurpose = Literal["reply", "reask", "upsell_take"]

_tracer = trace.get_tracer(__name__)


async def traced_complete(llm: LLMClient, messages: Sequence[LLMMessage], *, purpose: LLMPurpose) -> LLMResponse:
    stats = current_turn_stats()
    stats.llm_calls += 1
    provider, model = llm.provider_name, llm.model_name
    started = time.perf_counter()
    with _tracer.start_as_current_span(
        f"chat {model}",
        kind=SpanKind.CLIENT,
        attributes={
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": provider,
            "gen_ai.request.model": model,
            "sales.llm.purpose": purpose,
        },
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        try:
            response = await llm.complete(messages, json_mode=True)
        except Exception as exc:
            error_type = type(exc).__name__
            span.set_attribute("error.type", error_type)
            span.set_status(Status(StatusCode.ERROR))
            record_llm_call(
                provider=provider,
                model=model,
                purpose=purpose,
                seconds=time.perf_counter() - started,
                error_type=error_type,
                input_tokens=None,
                output_tokens=None,
            )
            raise

        usage = response.usage or {}
        input_tokens, output_tokens = usage.get("prompt_tokens"), usage.get("completion_tokens")
        if response.model:
            span.set_attribute("gen_ai.response.model", response.model)
        if response.finish_reason:
            span.set_attribute("gen_ai.response.finish_reasons", [response.finish_reason])
        if (response_id := (response.metadata or {}).get("id")) is not None:
            span.set_attribute("gen_ai.response.id", str(response_id))
        if input_tokens is not None:
            span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
            stats.input_tokens += input_tokens
        if output_tokens is not None:
            span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
            stats.output_tokens += output_tokens
        if response.cost is not None:
            span.set_attribute("llm.cost", response.cost)
            record_llm_cost(provider=provider, model=model, cost=response.cost)
        record_llm_call(
            provider=provider,
            model=model,
            purpose=purpose,
            seconds=time.perf_counter() - started,
            error_type=None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        return response
