"""The orchestrator, driven end to end with a scripted model and a fake price list.

This is where the Definition of Done is proved offline: a full walk from greeting
to a priced upsell, stages advancing in order with no illegal jump, and the upsell
total equal to what ops-core-api's own arithmetic would produce.
"""

from decimal import Decimal

import pytest

from src.app.contracts.conversation import Conversation
from src.app.contracts.sales import ALLOWED_TRANSITIONS, SalesStage
from src.app.exceptions.dialog import InvalidStageTransitionError
from src.app.repositories.memory_conversation import InMemoryConversationRepository
from src.app.services.dialog.decision import AgentDecision
from src.app.services.dialog.stages.base import DialogueStage
from src.app.services.sales.service import SalesService
from tests.fakes import FakePricingGateway, StubLLM, build_container, control


async def _drive(service: SalesService, messages: list[str], conversation_id: str | None = None) -> list:
    outcomes = []
    for message in messages:
        outcome = await service.take_turn(conversation_id, message)
        conversation_id = outcome.conversation_id
        outcomes.append(outcome)
    return outcomes


# Position in the canonical funnel. It lives here, not on SalesStage, because it
# exists only to express the "at most one jump" property below — no production
# path ever asks a stage for its index; the transitions are the table in
# contracts/sales.py.
_ORDER = {stage: index for index, stage in enumerate(SalesStage)}


def _assert_no_illegal_jump(stages: list[SalesStage]) -> None:
    for current, following in zip(stages[:-1], stages[1:], strict=True):
        assert following in ALLOWED_TRANSITIONS[current], f"{current} -> {following} is not a legal move"
        # "At most one jump": never skip more than a single stage in one turn.
        assert 0 <= _ORDER[following] - _ORDER[current] <= 2


async def test_full_funnel_reaches_a_correctly_priced_upsell() -> None:
    pricing = FakePricingGateway()
    llm = StubLLM(
        [
            control("Hi, I'm Alex from Aurora Wellness. What are you looking for today?"),
            control("Great — three deep tissue massages.", service="Deep Tissue Massage", quantity=3),
            control("That comes to 360 in total.", objection=False),
            control("Wonderful, glad it works.", objection=False),
            control("Book six for 648 and save 10% versus 720.", accept=True),
            control("Perfect — I'll set up six sessions.", accept=True),
        ]
    )
    service = build_container(llm, pricing).sales_service

    outcomes = await _drive(
        service,
        [
            "hi there",
            "I'd like three deep tissue massages",
            "how much is that?",
            "sounds good",
            "sure, tell me more",
            "yes, let's do six",
        ],
    )

    stages = [SalesStage.GREETING, *[outcome.stage for outcome in outcomes]]
    assert stages == [
        SalesStage.GREETING,
        SalesStage.QUALIFY,
        SalesStage.PRESENT,
        SalesStage.PRESENT,
        SalesStage.UPSELL,
        SalesStage.UPSELL,
        SalesStage.CLOSE,
    ]
    _assert_no_illegal_jump(stages)
    assert outcomes[-1].done is True

    # The DoD: the upsell price is the real, discounted total from the pricing
    # service — 6 x 120 = 720, less the 10% volume discount = 648.00.
    assert ("quote", "Deep Tissue Massage", "6") in pricing.calls
    conversation = await service._conversations.get(outcomes[-1].conversation_id)  # type: ignore[attr-defined]
    assert conversation is not None
    assert conversation.upsell_quote is not None
    assert conversation.upsell_quote.total == Decimal("648.00")


async def test_an_objection_is_handled_before_the_upsell() -> None:
    pricing = FakePricingGateway()
    llm = StubLLM(
        [
            control("Hello! What can I help you with?"),
            control("Two physiotherapy assessments, got it.", service="Physiotherapy Assessment", quantity=2),
            control("That's 280 in total.", objection=False),
            control("I understand, it's an investment.", objection=True),
            control("Buying a block brings the per-session price down.", resolved=True),
            control("For six you'd unlock a discount.", accept=False),
            control("No problem — two it is.", accept=False),
        ]
    )
    service = build_container(llm, pricing).sales_service

    outcomes = await _drive(
        service,
        ["hi", "two physio assessments", "cost?", "hmm, pricey", "okay that helps", "go on", "let's keep it at two"],
    )

    stages = [SalesStage.GREETING, *[outcome.stage for outcome in outcomes]]
    assert SalesStage.OBJECTION_HANDLING in stages
    _assert_no_illegal_jump(stages)
    # The objection path is the linear route, never a skip.
    assert stages.index(SalesStage.OBJECTION_HANDLING) < stages.index(SalesStage.UPSELL)


async def test_repeated_unparseable_output_escalates_to_a_bounded_handoff() -> None:
    pricing = FakePricingGateway()
    # The model never returns valid control JSON. max_parse_failures defaults to 2.
    service = build_container(StubLLM("I am not going to give you any JSON."), pricing).sales_service

    first = await service.take_turn(None, "hello")
    assert first.handoff is False  # first failure: re-ask, stay put
    assert first.stage is SalesStage.GREETING

    second = await service.take_turn(first.conversation_id, "hello again")
    assert second.handoff is True  # bounded: escalates instead of looping forever
    assert second.stage is SalesStage.GREETING


class _RogueStage(DialogueStage):
    """A stage whose route() returns a target it was never allowed to reach."""

    stage = SalesStage.GREETING

    def directive(self, conversation: Conversation) -> str:
        return "say anything"

    def route(self, conversation: Conversation, decision: AgentDecision) -> SalesStage:
        return SalesStage.CLOSE  # illegal from GREETING


async def test_an_illegal_transition_is_a_typed_error_not_a_crash(agent_settings) -> None:
    service = SalesService(
        InMemoryConversationRepository(ttl_seconds=3600.0, max_entries=100),
        {SalesStage.GREETING: _RogueStage(StubLLM(control("hi")), agent_settings)},
    )

    with pytest.raises(InvalidStageTransitionError) as caught:
        await service.take_turn(None, "hello")

    # Rendered as the uniform 500 envelope by the handler, never a raw stacktrace.
    assert caught.value.status_code == 500
    assert caught.value.error_code == "invalid_stage_transition"
