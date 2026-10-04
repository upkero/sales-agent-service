"""The orchestrator, driven end to end with a scripted model and a fake price list.

This is where the Definition of Done is proved offline: a full walk from greeting
to a priced upsell, stages advancing in order with no illegal jump, and the upsell
total equal to what ops-core-api's own arithmetic would produce.
"""

import asyncio
from collections.abc import Sequence
from decimal import Decimal

import pytest

from src.app.contracts.conversation import Conversation
from src.app.contracts.llm.llm_message import LLMMessage
from src.app.contracts.llm.llm_response import LLMResponse
from src.app.contracts.sales import ALLOWED_TRANSITIONS, SalesStage, TurnOutcome
from src.app.core.settings.agent import SalesAgentSettings
from src.app.exceptions.dialog import InvalidStageTransitionError
from src.app.exceptions.pricing import PricingUnavailableError
from src.app.prompts import get_prompt
from src.app.repositories.memory_conversation import InMemoryConversationRepository
from src.app.services.dialog.decision import AgentDecision
from src.app.services.dialog.stages.base import DialogueStage
from src.app.services.sales.service import SalesService
from tests.fakes import FakePricingGateway, StubLLM, UnavailablePricingGateway, build_container, control, verdict


async def _drive(
    service: SalesService,
    messages: list[str],
    conversation_id: str | None = None,
) -> list[TurnOutcome]:
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
            # One guest message, two model calls: QUALIFY fills the slots, then PRESENT
            # answers in the same turn, so the price is in the reply the guest hears.
            control("Great — three deep tissue massages.", service="Deep Tissue Massage", quantity=3),
            control("That comes to 360 in total.", objection=False),
            # One guest message, two model calls again: PRESENT reads "sounds good",
            # then UPSELL makes its offer in the same turn.
            control("Wonderful, glad it works.", objection=False),
            control("Book six for 648 and save 10% versus 720."),
            verdict(True),  # "yes, let's do six" is read on its own first...
            control("Perfect, six it is. May I have your name and a phone or email?"),
            control("Thank you, Dana! The front desk will email you.", name="Dana", contact="dana@example.com"),
        ]
    )
    service = build_container(llm, pricing).sales_service

    outcomes = await _drive(
        service,
        [
            "hi there",
            "I'd like three deep tissue massages",
            "sounds good",
            "yes, let's do six",
            "Dana, dana@example.com",
        ],
    )

    stages = [SalesStage.GREETING, *[outcome.stage for outcome in outcomes]]
    assert stages == [
        SalesStage.GREETING,
        SalesStage.QUALIFY,
        SalesStage.PRESENT,
        SalesStage.UPSELL,
        SalesStage.CLOSE,
        SalesStage.CLOSE,
    ]
    assert outcomes[1].reply == "That comes to 360 in total."
    # The offer answers "sounds good"; a dead-end "glad it works" would push it
    # onto the guest's next message, usually their contact details.
    assert outcomes[2].reply == "Book six for 648 and save 10% versus 720."
    _assert_no_illegal_jump(stages)
    assert outcomes[-2].done is False  # asked for the contact
    assert outcomes[-1].done is True  # confirmed the order once and closed

    # The DoD: the upsell price is the real, discounted total from the pricing
    # service — 6 x 120 = 720, less the 10% volume discount = 648.00.
    assert ("quote", "Deep Tissue Massage", "6") in pricing.calls
    conversation = await service._conversations.get(outcomes[-1].conversation_id)
    assert conversation is not None
    assert conversation.upsell_quote is not None
    assert conversation.upsell_quote.total == Decimal("648.00")
    # "Yes, let's do six" made the upsell the order the funnel closes on.
    assert conversation.accepted_offer == "upsell"
    assert conversation.quote == conversation.upsell_quote


async def test_after_agreeing_the_agent_asks_for_a_contact_once_then_closes() -> None:
    # The model as seen live: the same line on every turn once the prospect agreed.
    same_line = control("The front desk will confirm the booking shortly.", accept=False)
    llm = StubLLM(
        [
            control("Hi!"),
            control("Three, got it.", service="Deep Tissue Massage", quantity=3),
            control("That comes to 360.00.", objection=False),
            same_line,  # PRESENT reads the agreement...
            same_line,  # ...and UPSELL makes its offer in the same turn
            verdict(False),  # UPSELL reads the answer...
            same_line,  # ...and acknowledges it
            same_line,  # CLOSE confirms
            same_line,  # CLOSE, after the conversation is over
        ]
    )
    service = build_container(llm, FakePricingGateway()).sales_service

    outcomes = await _drive(
        service,
        ["hi", "three massages", "OK, sounds good", "no, three is fine", "Dana, 555-0100", "thanks"],
    )

    # Agreement -> one ask for the contact -> one closing message -> done.
    assert [(outcome.stage, outcome.done) for outcome in outcomes[2:]] == [
        (SalesStage.UPSELL, False),  # the offer
        (SalesStage.CLOSE, False),  # the answer: asks for a name and a contact
        (SalesStage.CLOSE, True),  # the confirmation: closed
        (SalesStage.CLOSE, True),
    ]
    system_prompts = [call[0].content for call in llm.calls]
    assert sum("phone number or email" in prompt for prompt in system_prompts) == 1
    assert sum("Their order:" in prompt for prompt in system_prompts) == 1
    # Not an instruction on every turn any more, which is what made the line repeat.
    assert "front desk" not in get_prompt("persona").text
    conversation = await service._conversations.get(outcomes[-1].conversation_id)
    assert conversation is not None
    assert conversation.accepted_offer == "base"
    assert conversation.closed is True


async def test_an_objection_is_handled_before_the_upsell() -> None:
    pricing = FakePricingGateway()
    llm = StubLLM(
        [
            control("Hello! What can I help you with?"),
            control("Two physiotherapy assessments, got it.", service="Physiotherapy Assessment", quantity=2),
            control("That's 280 in total.", objection=False),
            control("I understand, it's an investment.", objection=True),  # PRESENT reads the concern...
            control("Buying a block brings the per-session price down.", resolved=False),  # ...OBJECTION answers it
            control("Glad that helps.", resolved=True),  # OBJECTION reads that it is settled...
            control("Six would be 756.00 with the discount."),  # ...UPSELL offers
            verdict(False),
            control("No problem — two it is."),
        ]
    )
    service = build_container(llm, pricing).sales_service

    outcomes = await _drive(
        service,
        ["hi", "two physio assessments", "hmm, pricey", "okay that helps", "let's keep it at two"],
    )

    stages = [SalesStage.GREETING, *[outcome.stage for outcome in outcomes]]
    assert SalesStage.OBJECTION_HANDLING in stages
    _assert_no_illegal_jump(stages)
    # The objection path is the linear route, never a skip.
    assert stages.index(SalesStage.OBJECTION_HANDLING) < stages.index(SalesStage.UPSELL)
    # Each reaction is answered by the stage it leads to, not by a dead end.
    assert outcomes[2].reply == "Buying a block brings the per-session price down."
    assert outcomes[3].reply == "Six would be 756.00 with the discount."


async def test_a_concern_settled_in_one_message_still_gets_the_offer_in_that_reply() -> None:
    # As seen live (RU): "no thanks, six is enough" read as a concern and settled at
    # once. With one hand-over the offer slid onto the next message, the contact.
    llm = StubLLM(
        [
            control("Hello!"),
            control("Six, got it.", service="Deep Tissue Massage", quantity=6),
            control("That's 648.00 in total.", objection=False),
            control("(reaction read)", objection=True),  # PRESENT
            control("(concern answered)", resolved=True),  # OBJECTION_HANDLING
            control("For twenty you'd pay 2040.00 instead."),  # UPSELL offers
        ]
    )
    service = build_container(llm, FakePricingGateway()).sales_service

    outcomes = await _drive(service, ["hi", "six deep tissue massages", "no thanks, six is enough"])

    assert outcomes[-1].stage is SalesStage.UPSELL
    assert outcomes[-1].reply == "For twenty you'd pay 2040.00 instead."
    conversation = await service._conversations.get(outcomes[-1].conversation_id)
    assert conversation is not None and conversation.upsell_offered is True


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


async def test_a_failed_turn_leaves_the_stored_conversation_untouched() -> None:
    llm = StubLLM(
        [
            control("Hi! What can I help with?"),
            control("Three, got it.", service="Deep Tissue Massage", quantity=3),
        ]
    )
    service = build_container(llm, UnavailablePricingGateway()).sales_service
    first = await service.take_turn(None, "hi")

    # QUALIFY fills the slots, then PRESENT cannot reach the price list.
    with pytest.raises(PricingUnavailableError):
        await service.take_turn(first.conversation_id, "three deep tissue massages")

    stored = await service._conversations.get(first.conversation_id)
    assert stored is not None
    assert stored.stage is SalesStage.QUALIFY  # not advanced to PRESENT
    assert len(stored.messages) == 2  # the failed message is not in the history
    assert (stored.service, stored.quantity) == (None, None)


class _SlowLLM(StubLLM):
    """Yields to the event loop mid-call, the way a real network call does."""

    async def complete(self, messages: Sequence[LLMMessage], *, json_mode: bool = False) -> LLMResponse:
        await asyncio.sleep(0)
        return await super().complete(messages, json_mode=json_mode)


async def test_concurrent_turns_on_one_conversation_both_land() -> None:
    service = build_container(_SlowLLM(control("Hello!")), FakePricingGateway()).sales_service
    first = await service.take_turn(None, "hi")

    await asyncio.gather(
        service.take_turn(first.conversation_id, "one"),
        service.take_turn(first.conversation_id, "two"),
    )

    stored = await service._conversations.get(first.conversation_id)
    assert stored is not None
    # Unserialised, both turns copy the same state and the later save drops the other.
    assert [message.content for message in stored.messages if message.role == "user"] == ["hi", "one", "two"]


async def test_an_unknown_id_starts_a_conversation_under_a_server_minted_id() -> None:
    service = build_container(StubLLM(control("Hello!")), FakePricingGateway()).sales_service

    outcome = await service.take_turn("1", "hello")

    # A miss is a miss: the caller's id is not adopted, or two clients that both
    # guessed "1" would be talking into the same conversation. The frontend is
    # unaffected — it continues with whatever id came back.
    assert outcome.conversation_id != "1"
    assert await service.take_turn(outcome.conversation_id, "still me") is not None


class _RogueStage(DialogueStage):
    """A stage whose route() returns a target it was never allowed to reach."""

    stage = SalesStage.GREETING

    def directive(self, conversation: Conversation) -> str:
        return "say anything"

    def route(self, conversation: Conversation, decision: AgentDecision) -> SalesStage:
        return SalesStage.CLOSE  # illegal from GREETING


async def test_an_illegal_transition_is_a_typed_error_not_a_crash(agent_settings: SalesAgentSettings) -> None:
    service = SalesService(
        InMemoryConversationRepository(ttl_seconds=3600.0, max_entries=100),
        {SalesStage.GREETING: _RogueStage(StubLLM(control("hi")), agent_settings)},
    )

    with pytest.raises(InvalidStageTransitionError) as caught:
        await service.take_turn(None, "hello")

    # Rendered as the uniform 500 envelope by the handler, never a raw stacktrace.
    assert caught.value.status_code == 500
    assert caught.value.error_code == "invalid_stage_transition"


async def test_an_answer_with_contact_details_is_confirmed_in_the_same_reply() -> None:
    # As seen live: the answer turn asked for details just given, and CLOSE then
    # repeated that line on the next message without confirming anything.
    llm = StubLLM(
        [
            control("Hello!"),
            control("Six, got it.", service="Deep Tissue Massage", quantity=6),
            control("That's 648.00 in total.", objection=False),
            control("(reaction read)", objection=False),  # PRESENT
            control("Twenty would be 2040.00."),  # UPSELL offers
            verdict(False),  # the answer does not take it...
            control("(would ask for details)"),  # ...UPSELL's reply, replaced
            control("Thanks, Dana! The front desk will be in touch.", name="Dana", contact="dana@example.com"),
        ]
    )
    service = build_container(llm, FakePricingGateway()).sales_service

    outcomes = await _drive(service, ["hi", "six deep tissue massages", "ok", "Dana, dana@example.com"])

    assert outcomes[-1].stage is SalesStage.CLOSE
    assert outcomes[-1].done is True
    assert outcomes[-1].reply == (
        "Thanks, Dana! The front desk will be in touch. For 6 × Deep Tissue Massage the total is 648.00."
    )
