"""What an operator reads when a conversation goes wrong: spans, counters, log fields.

Every case drives the real service, stages and middleware; only the LLM and
ops-core-api are the fakes the rest of the suite uses.
"""

import logging
from collections import Counter
from collections.abc import Iterator, Sequence
from decimal import Decimal
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from opentelemetry import trace

from src.app.api.v1.middleware.rate_limit import _masked
from src.app.bootstrap.container import _count_if_lost
from src.app.contracts.conversation import Conversation
from src.app.contracts.llm.llm_message import LLMMessage
from src.app.contracts.llm.llm_response import LLMResponse
from src.app.contracts.sales import SalesStage
from src.app.core.logging import _RequestIdFilter
from src.app.core.settings.agent import SalesAgentSettings
from src.app.core.turn_context import set_conversation_id
from src.app.exceptions.dialog import InvalidStageTransitionError
from src.app.exceptions.llm import LLMGenerationError
from src.app.repositories.memory_conversation import InMemoryConversationRepository
from src.app.services.dialog.decision import AgentDecision
from src.app.services.dialog.stages.base import DialogueStage
from src.app.services.dialog.stages.present import PresentStage
from src.app.services.sales.service import SalesService
from src.main import create_app
from tests import telemetry
from tests.fakes import (
    STUB_USAGE,
    FakePricingGateway,
    StubLLM,
    build_container,
    compute_quote,
    control,
    verdict,
)


@pytest.fixture
def spans() -> Iterator[None]:
    telemetry.clear_spans()
    yield
    telemetry.clear_spans()


def _count(name: str, *labels: str) -> Counter[tuple[str, ...]]:
    """A counter's values keyed by the given label values (cumulative: take a before and subtract)."""
    counts: Counter[tuple[str, ...]] = Counter()
    for point in telemetry.points(name):
        counts[tuple(point.attributes[label] for label in labels)] += point.value
    return counts


_TRANSITION_LABELS = ("from_stage", "to_stage", "path")
_OUTCOME_LABELS = ("outcome", "accepted_offer", "last_stage")

# Four guest messages, eight model calls: the shape test_sales_service walks as well.
_JOURNEY = [
    control("Hello!"),
    control("Six, got it.", service="Deep Tissue Massage", quantity=6),
    control("That's 648.00 in total.", objection=False),  # PRESENT states the price
    control("(reaction read)", objection=False),  # PRESENT reads "ok"
    control("Twenty would be 2040.00."),  # UPSELL offers
    verdict(False),  # the answer does not take it
    control("(would ask for details)"),  # UPSELL's reply, replaced
    control("Thanks, Dana! The front desk will be in touch.", name="Dana", contact="dana@example.com"),
]
_MESSAGES = ["hi", "six deep tissue massages", "ok", "Dana, dana@example.com"]


async def _walk(service: SalesService) -> str:
    conversation_id = None
    for message in _MESSAGES:
        conversation_id = (await service.take_turn(conversation_id, message)).conversation_id
    assert conversation_id is not None
    return conversation_id


async def test_a_conversation_from_greeting_to_close_counts_exactly_its_transitions(
    spans: None,
) -> None:
    before = _count("sales.stage.transitions", *_TRANSITION_LABELS)
    outcomes_before = _count("sales.outcomes", *_OUTCOME_LABELS)
    service = build_container(StubLLM(list(_JOURNEY)), FakePricingGateway()).sales_service

    await _walk(service)

    assert _count("sales.stage.transitions", *_TRANSITION_LABELS) - before == {
        ("greeting", "qualify", "final"): 1,
        ("qualify", "present", "requote"): 1,  # both slots filled in one message: priced in that same turn
        ("present", "present", "final"): 1,  # the price is stated, then the reaction awaited
        ("present", "upsell", "handover"): 1,  # "ok" is read, and the upsell offer goes in the same reply
        ("upsell", "upsell", "final"): 1,
        ("upsell", "close", "handover"): 1,  # contact details given with the answer
        ("close", "close", "final"): 1,
    }
    assert _count("sales.outcomes", *_OUTCOME_LABELS) - outcomes_before == {("closed", "base", "close"): 1}


async def test_every_model_call_is_a_span_with_its_usage_and_purpose(spans: None) -> None:
    duration_before = telemetry.reading("gen_ai.client.operation.duration")
    service = build_container(StubLLM(list(_JOURNEY)), FakePricingGateway()).sales_service

    await _walk(service)

    chats = [span for span in telemetry.finished_spans() if span.name == "chat stub-model"]
    assert len(chats) == len(_JOURNEY)
    for span in chats:
        assert span.attributes is not None
        assert span.attributes["gen_ai.operation.name"] == "chat"
        assert span.attributes["gen_ai.usage.input_tokens"] == STUB_USAGE["prompt_tokens"]
        assert span.attributes["gen_ai.usage.output_tokens"] == STUB_USAGE["completion_tokens"]
    purposes = Counter(span.attributes["sales.llm.purpose"] for span in chats if span.attributes)
    assert purposes == {"reply": 7, "upsell_take": 1}
    assert telemetry.reading("gen_ai.client.operation.duration") - duration_before == len(_JOURNEY)


async def test_a_reask_is_labelled_as_one(spans: None) -> None:
    service = build_container(StubLLM(["not json", control("Hello!")]), FakePricingGateway()).sales_service

    await service.take_turn(None, "hi")

    chats = [span for span in telemetry.finished_spans() if span.name.startswith("chat ")]
    assert [span.attributes["sales.llm.purpose"] for span in chats if span.attributes] == ["reply", "reask"]


def _attribute_values(span: Any) -> list[Any]:
    """Every value on a span, its events' included."""
    events = [value for event in span.events for value in (event.attributes or {}).values()]
    return [*(span.attributes or {}).values(), *events]


async def test_the_customers_words_never_reach_a_span(spans: None) -> None:
    service = build_container(StubLLM(list(_JOURNEY)), FakePricingGateway()).sales_service

    await _walk(service)

    values = [
        str(value)
        for span in telemetry.finished_spans()
        for value in _attribute_values(span)
    ]
    assert values
    for secret in ("Dana", "dana@example.com", "six deep tissue", "front desk", "Twenty would be"):
        assert not any(secret in value for value in values), secret


async def test_the_turn_reports_what_it_cost_on_one_line_and_on_its_span(
    spans: None, caplog: pytest.LogCaptureFixture
) -> None:
    service = build_container(StubLLM(list(_JOURNEY)), FakePricingGateway()).sales_service

    with caplog.at_level(logging.INFO):
        await _walk(service)

    lines = [record.__dict__ for record in caplog.records if record.getMessage() == "turn_completed"]
    assert len(lines) == 4
    assert (lines[-1]["from_stage"], lines[-1]["to_stage"], lines[-1]["done"]) == ("upsell", "close", True)
    assert lines[-1]["llm_calls"] == 3  # the offer read, the upsell reply, the close reply
    assert lines[-1]["input_tokens"] == 3 * STUB_USAGE["prompt_tokens"]
    assert lines[-1]["output_tokens"] == 3 * STUB_USAGE["completion_tokens"]
    assert isinstance(lines[-1]["duration_ms"], int)
    assert "dana@example.com" not in str(lines)


async def test_a_failed_model_call_is_recorded_by_its_type_only(spans: None) -> None:
    class Failing(StubLLM):
        async def complete(self, messages: Sequence[LLMMessage], *, json_mode: bool = False) -> LLMResponse:
            raise LLMGenerationError("secret provider detail")

    service = build_container(Failing("unused"), FakePricingGateway()).sales_service

    with pytest.raises(LLMGenerationError):
        await service.take_turn(None, "hi")

    (chat,) = (span for span in telemetry.finished_spans() if span.name == "chat stub-model")
    assert chat.attributes is not None
    assert chat.attributes["error.type"] == "LLMGenerationError"
    assert chat.status.status_code is trace.StatusCode.ERROR
    assert chat.events == ()  # no exception event: its text is the provider's, not ours


async def test_a_handoff_is_counted_once_however_often_the_model_fails() -> None:
    before = _count("sales.outcomes", *_OUTCOME_LABELS)
    service = build_container(StubLLM("no json, ever"), FakePricingGateway()).sales_service

    first = await service.take_turn(None, "hello")
    second = await service.take_turn(first.conversation_id, "hello?")
    third = await service.take_turn(first.conversation_id, "hello??")

    assert second.handoff is True and third.handoff is True
    assert _count("sales.outcomes", *_OUTCOME_LABELS) - before == {("handoff", "none", "greeting"): 1}


async def test_an_abandoned_conversation_is_an_eviction_but_an_ended_one_is_not() -> None:
    before = _count("sales.outcomes", *_OUTCOME_LABELS)
    now = [0.0]
    repository = InMemoryConversationRepository(
        ttl_seconds=10.0, max_entries=100, clock=lambda: now[0], on_evict=_count_if_lost
    )
    await repository.save(Conversation(id="walked-away", stage=SalesStage.PRESENT))
    await repository.save(Conversation(id="bought", stage=SalesStage.CLOSE, closed=True))
    await repository.save(Conversation(id="escalated", stage=SalesStage.QUALIFY, handed_off=True))

    now[0] = 60.0
    await repository.save(Conversation(id="fresh"))  # the sweep on write drops all three
    assert await repository.get("fresh") is not None

    assert _count("sales.outcomes", *_OUTCOME_LABELS) - before == {("evicted", "none", "present"): 1}


async def test_the_size_cap_evicts_through_the_same_door() -> None:
    before = _count("sales.outcomes", *_OUTCOME_LABELS)
    repository = InMemoryConversationRepository(ttl_seconds=3600.0, max_entries=1, on_evict=_count_if_lost)

    await repository.save(Conversation(id="first", stage=SalesStage.UPSELL))
    await repository.save(Conversation(id="second"))

    assert _count("sales.outcomes", *_OUTCOME_LABELS) - before == {("evicted", "none", "upsell"): 1}


class _RogueStage(DialogueStage):
    stage = SalesStage.GREETING

    def directive(self, conversation: Conversation) -> str:
        return "say anything"

    def route(self, conversation: Conversation, decision: AgentDecision) -> SalesStage:
        return SalesStage.CLOSE  # illegal from GREETING


async def test_an_illegal_transition_is_counted_and_not_counted_as_a_transition(
    agent_settings: SalesAgentSettings,
) -> None:
    illegal_before = _count("sales.illegal_transitions", "from_stage", "to_stage")
    moves_before = _count("sales.stage.transitions", *_TRANSITION_LABELS)
    service = SalesService(
        InMemoryConversationRepository(ttl_seconds=3600.0, max_entries=100),
        {SalesStage.GREETING: _RogueStage(StubLLM(control("hi")), agent_settings)},
    )

    with pytest.raises(InvalidStageTransitionError):
        await service.take_turn(None, "hello")

    assert _count("sales.illegal_transitions", "from_stage", "to_stage") - illegal_before == {("greeting", "close"): 1}
    assert _count("sales.stage.transitions", *_TRANSITION_LABELS) == moves_before


async def test_a_reply_that_states_an_unquoted_amount_is_counted_as_replaced(
    agent_settings: SalesAgentSettings, pricing: FakePricingGateway
) -> None:
    before = _count("sales.guardrails", "kind", "stage")
    conversation = Conversation(id="c1", stage=SalesStage.PRESENT, service="Deep Tissue Massage", quantity=3)
    conversation.add_user("how much?")
    stage = PresentStage(StubLLM(control("That comes to 999 in total.", objection=False)), agent_settings, pricing)

    await stage.handle(conversation)

    assert _count("sales.guardrails", "kind", "stage") - before == {("amount_replaced", "present"): 1}


async def test_a_reply_that_leaves_the_price_out_gets_it_injected_and_counted(
    agent_settings: SalesAgentSettings, pricing: FakePricingGateway
) -> None:
    before = _count("sales.guardrails", "kind", "stage")
    conversation = Conversation(id="c1", stage=SalesStage.PRESENT, service="Deep Tissue Massage", quantity=3)
    conversation.quote = compute_quote("Deep Tissue Massage", Decimal("120.00"), 3)
    conversation.add_user("how much?")
    stage = PresentStage(StubLLM(control("Happy to help with that.", objection=False)), agent_settings, pricing)

    await stage.handle(conversation)

    assert _count("sales.guardrails", "kind", "stage") - before == {("total_injected", "present"): 1}


def test_a_log_line_in_a_turn_names_its_conversation_and_its_trace() -> None:
    tracer = trace.get_tracer(__name__)
    record = logging.LogRecord("t", logging.INFO, "", 0, "m", (), None)
    set_conversation_id("conv-42")
    try:
        with tracer.start_as_current_span("outer") as span:
            _RequestIdFilter().filter(record)
    finally:
        set_conversation_id("")  # a ContextVar set here would otherwise name every later test's lines

    assert record.__dict__["conversation_id"] == "conv-42"
    assert record.__dict__["trace_id"] == format(span.get_span_context().trace_id, "032x")
    assert record.__dict__["span_id"] == format(span.get_span_context().span_id, "016x")

    explicit = logging.LogRecord("t", logging.INFO, "", 0, "m", (), None)
    explicit.conversation_id = "from-the-call-site"
    _RequestIdFilter().filter(explicit)
    assert explicit.__dict__["conversation_id"] == "from-the-call-site"


@pytest.mark.parametrize(
    ("address", "masked"),
    [
        ("203.0.113.57", "203.0.113.0/24"),
        ("2001:db8:abcd:1234::1", "2001:db8:abcd::/48"),
        ("unknown", "unknown"),
    ],
)
def test_a_client_is_logged_as_its_network(address: str, masked: str) -> None:
    assert _masked(address) == masked


async def test_the_rate_limit_log_never_carries_the_clients_address(caplog: pytest.LogCaptureFixture) -> None:
    app = create_app()
    logging.getLogger().addHandler(caplog.handler)  # create_app() replaced the root handlers, caplog's included
    app.state.container = build_container(StubLLM(control("hi")), FakePricingGateway())
    headers = {"X-API-Key": "test-inbound-key-1234567890"}
    transport = ASGITransport(app=app, client=("198.51.100.77", 5000))

    with caplog.at_level(logging.WARNING):
        async with AsyncClient(transport=transport, base_url="http://testserver", headers=headers) as client:
            statuses = [(await client.post("/api/v1/turn", json={"message": "hi"})).status_code for _ in range(8)]

    assert 429 in statuses
    limited = [record for record in caplog.records if "rate limit exceeded" in record.getMessage()]
    assert limited
    assert "198.51.100.77" not in str([record.__dict__ for record in limited])
    assert limited[0].__dict__["client"] == "198.51.100.0/24"
