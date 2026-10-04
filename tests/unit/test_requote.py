"""A change of quantity or service after the price was stated is priced again by
ops-core-api before the agent answers, so every total the prospect hears comes
from the pricing service and none from the model's own arithmetic."""

from decimal import Decimal

from src.app.contracts.sales import SalesStage, TurnOutcome
from src.app.services.sales.service import SalesService
from tests.fakes import FakePricingGateway, StubLLM, build_container, control, verdict

# Turns 1-3 of every scenario: greet, qualify three deep tissue massages (PRESENT
# answers in the same turn with 3 x 120 = 360.00).
_PRICED_AT_THREE = [
    control("Hi, I'm Alex. What brings you in?"),
    control("Three deep tissue massages, got it.", service="Deep Tissue Massage", quantity=3),
    control("Three sessions come to 360.00 in total.", objection=False),
]


async def _drive(service: SalesService, messages: list[str]) -> list[TurnOutcome]:
    outcomes: list[TurnOutcome] = []
    conversation_id: str | None = None
    for message in messages:
        outcome = await service.take_turn(conversation_id, message)
        conversation_id = outcome.conversation_id
        outcomes.append(outcome)
    return outcomes


def _last_system_prompt(llm: StubLLM) -> str:
    return llm.calls[-1][0].content


async def _requote_after_present(reaction: str, total: str, **slots: object) -> None:
    pricing = FakePricingGateway()
    llm = StubLLM(
        [
            *_PRICED_AT_THREE,
            # The reaction turn: the model notices the change but has no price for it.
            control("Let me check that for you.", objection=False, **slots),
            # PRESENT again, in the same turn, with the fresh quote in its prompt.
            control(f"That comes to {total} in total.", objection=False),
        ]
    )
    service = build_container(llm, pricing).sales_service

    outcomes = await _drive(service, ["hi", "three deep tissue massages", reaction])

    last = outcomes[-1]
    assert last.stage is SalesStage.PRESENT  # re-presented, not carried on to the upsell
    assert last.reply == f"That comes to {total} in total."
    assert total in _last_system_prompt(llm)  # the model was handed the number, not asked for it
    conversation = await service._conversations.get(last.conversation_id)
    assert conversation is not None and conversation.quote is not None
    assert conversation.quote.total == Decimal(total)
    assert conversation.price_presented is True


async def test_three_to_eight_sessions_is_priced_by_ops_core() -> None:
    # 8 x 120 = 960.00 less the 10% tier = 864.00; the model once said 960.00.
    await _requote_after_present("actually make it 8 sessions", "864.00", quantity=8)


async def test_three_to_five_sessions_is_priced_by_ops_core() -> None:
    await _requote_after_present("can we do 5 instead?", "600.00", quantity=5)


async def test_a_different_service_is_priced_by_ops_core() -> None:
    await _requote_after_present(
        "hmm, sports recovery instead",
        "330.00",  # 3 x 110, no discount
        service="Sports Recovery Session",
    )


async def test_the_requote_asks_ops_core_for_the_new_quantity() -> None:
    pricing = FakePricingGateway()
    llm = StubLLM(
        [
            *_PRICED_AT_THREE,
            control("Let me check.", objection=False, quantity=8),
            control("That comes to 864.00 in total.", objection=False),
        ]
    )
    service = build_container(llm, pricing).sales_service

    await _drive(service, ["hi", "three deep tissue massages", "make it 8"])

    assert ("quote", "Deep Tissue Massage", "8") in pricing.calls


async def test_a_change_in_answer_to_the_upsell_is_priced_again() -> None:
    pricing = FakePricingGateway()
    llm = StubLLM(
        [
            *_PRICED_AT_THREE,
            control("Great.", objection=False),
            control("Six would be 648.00 instead of 720.00."),
            verdict(False),
            control("Sure.", quantity=8),
            control("Eight sessions come to 864.00 in total.", objection=False),
        ]
    )
    service = build_container(llm, pricing).sales_service

    outcomes = await _drive(
        service,
        ["hi", "three deep tissue massages", "sounds good", "neither, make it 8"],
    )

    assert [outcome.stage for outcome in outcomes[-2:]] == [SalesStage.UPSELL, SalesStage.PRESENT]
    assert outcomes[-2].reply == "Six would be 648.00 instead of 720.00."  # offered on "sounds good"
    assert outcomes[-1].reply == "Eight sessions come to 864.00 in total."
    conversation = await service._conversations.get(outcomes[-1].conversation_id)
    assert conversation is not None and conversation.quote is not None
    assert conversation.quote.quantity == 8
    # The old offer was for three; the upsell is made again from the new order.
    assert conversation.upsell_quote is None
    assert conversation.upsell_offered is False


async def test_a_change_during_an_objection_is_priced_again() -> None:
    pricing = FakePricingGateway()
    llm = StubLLM(
        [
            *_PRICED_AT_THREE,
            control("I hear you.", objection=True),
            control("Good thinking.", resolved=False, quantity=8),
            control("Eight sessions come to 864.00 in total.", objection=False),
        ]
    )
    service = build_container(llm, pricing).sales_service

    outcomes = await _drive(
        service,
        ["hi", "three deep tissue massages", "too pricey", "what if I take 8?"],
    )

    assert outcomes[-1].stage is SalesStage.PRESENT
    assert "864.00" in outcomes[-1].reply


async def test_the_offered_upsell_quantity_is_not_priced_again() -> None:
    # "Yes, six" names the quantity the upsell already priced; that is not a change.
    pricing = FakePricingGateway()
    llm = StubLLM(
        [
            *_PRICED_AT_THREE,
            control("Great.", objection=False),
            control("Six would be 648.00."),
            verdict(True),
            control("Wonderful.", quantity=6),
        ]
    )
    service = build_container(llm, pricing).sales_service

    outcomes = await _drive(service, ["hi", "three deep tissue massages", "sounds good", "yes, six"])

    assert outcomes[-1].stage is not SalesStage.PRESENT
    assert pricing.calls.count(("quote", "Deep Tissue Massage", "6")) == 1


async def test_a_total_the_quote_does_not_contain_never_reaches_the_prospect() -> None:
    pricing = FakePricingGateway()
    llm = StubLLM(
        [
            *_PRICED_AT_THREE,
            control("Let me check.", objection=False, quantity=8),
            # Handed 864.00, the model still does its own arithmetic.
            control("Eight sessions at 120.00 each come to 960.00.", objection=False),
        ]
    )
    service = build_container(llm, pricing).sales_service

    outcomes = await _drive(service, ["hi", "three deep tissue massages", "make it 8"])

    assert "960" not in outcomes[-1].reply
    assert "864.00" in outcomes[-1].reply
