"""OpenTelemetry metrics: the MeterProvider, and every instrument this service writes.

Instruments live here, once, so a label set is defined in one place and a
dashboard query has one file to be checked against. Labels are bounded by
construction: stage names, a few fixed outcomes, model and provider. Never a
conversation id, a message, a service name or a quantity, which is model free text
until the catalogue snaps it.

The MeterProvider is installed by core/telemetry.py (`setup_metrics`); instruments
made here against the global meter attach to it whenever it appears.
"""

from typing import Literal

from opentelemetry import metrics

from src.app.contracts.conversation import Conversation

Outcome = Literal["closed", "handoff", "evicted"]
GuardrailKind = Literal[
    "amount_replaced", "total_injected", "unknown_service", "catalogue_unavailable", "quantity_defaulted"
]
TransitionPath = Literal["final", "handover", "requote"]


_meter = metrics.get_meter("sales-agent-service")

_transitions = _meter.create_counter(
    "sales.stage.transitions", description="Every move the state machine makes, including staying put."
)
_illegal_transitions = _meter.create_counter(
    "sales.illegal_transitions", description="Moves a stage was not allowed to make: each one is a bug."
)
_outcomes = _meter.create_counter(
    "sales.outcomes", description="How a conversation ended. 'evicted' is the lost sale, by the stage it stopped at."
)
_guardrails = _meter.create_counter(
    "sales.guardrails", description="Times the code corrected, or worked around, what the model said."
)
_llm_duration = _meter.create_histogram(
    "gen_ai.client.operation.duration", unit="s", description="One model call, as the stage saw it."
)
_llm_tokens = _meter.create_histogram(
    "gen_ai.client.token.usage", unit="{token}", description="Tokens a model call used."
)
_llm_cost = _meter.create_counter(
    "llm.cost", description="LLM spend in USD, as reported by the provider (OpenRouter usage.cost)."
)


def record_transition(from_stage: str, to_stage: str, path: TransitionPath) -> None:
    _transitions.add(1, {"from_stage": from_stage, "to_stage": to_stage, "path": path})


def record_illegal_transition(from_stage: str, to_stage: str) -> None:
    _illegal_transitions.add(1, {"from_stage": from_stage, "to_stage": to_stage})


def record_outcome(outcome: Outcome, conversation: Conversation) -> None:
    _outcomes.add(
        1,
        {
            "outcome": outcome,
            "accepted_offer": conversation.accepted_offer or "none",
            "last_stage": conversation.stage.value,
        },
    )


def record_guardrail(kind: GuardrailKind, stage: str) -> None:
    _guardrails.add(1, {"kind": kind, "stage": stage})


def record_llm_call(
    *,
    provider: str,
    model: str,
    purpose: str,
    seconds: float,
    error_type: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
) -> None:
    attributes = {"gen_ai.provider.name": provider, "gen_ai.request.model": model, "purpose": purpose}
    _llm_duration.record(seconds, {**attributes, "error.type": error_type} if error_type else attributes)
    for token_type, count in (("input", input_tokens), ("output", output_tokens)):
        if count is not None:
            _llm_tokens.record(count, {**attributes, "gen_ai.token.type": token_type})


def record_llm_cost(*, provider: str, model: str, cost: float) -> None:
    _llm_cost.add(cost, {"provider": provider, "model": model})
