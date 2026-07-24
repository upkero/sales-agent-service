from decimal import Decimal

from src.app.contracts.conversation import Conversation
from src.app.contracts.sales import SalesStage
from src.app.services.dialog.stages.present import PresentStage
from tests.fakes import FakePricingGateway, StubLLM, compute_quote, control


def _priced_conversation() -> Conversation:
    conversation = Conversation(id="c1", stage=SalesStage.PRESENT, service="Deep Tissue Massage", quantity=3)
    conversation.add_user("how much would that be?")
    return conversation


async def test_fetches_the_price_states_it_and_waits_for_a_reaction(
    agent_settings, pricing: FakePricingGateway
) -> None:
    stage = PresentStage(StubLLM(control("That comes to 360 in total.", objection=False)), agent_settings, pricing)
    conversation = _priced_conversation()

    result = await stage.handle(conversation)

    assert conversation.quote is not None
    assert conversation.quote.total == Decimal("360.00")  # 3 x 120, no discount
    assert conversation.price_presented is True
    # Presents on this turn, reads the reaction on the next — so it stays for now.
    assert result.next_stage is SalesStage.PRESENT
    assert ("quote", "Deep Tissue Massage", "3") in pricing.calls


async def test_no_objection_skips_straight_to_upsell(agent_settings, pricing: FakePricingGateway) -> None:
    conversation = _priced_conversation()
    conversation.quote = compute_quote("Deep Tissue Massage", Decimal("120.00"), 3)
    conversation.price_presented = True  # the price was stated last turn
    stage = PresentStage(StubLLM(control("Glad it works for you!", objection=False)), agent_settings, pricing)

    result = await stage.handle(conversation)

    # The sanctioned single skip past objection handling.
    assert result.next_stage is SalesStage.UPSELL


async def test_an_objection_routes_to_objection_handling(agent_settings, pricing: FakePricingGateway) -> None:
    conversation = _priced_conversation()
    conversation.quote = compute_quote("Deep Tissue Massage", Decimal("120.00"), 3)
    conversation.price_presented = True
    stage = PresentStage(StubLLM(control("I hear you on the price.", objection=True)), agent_settings, pricing)

    result = await stage.handle(conversation)

    assert result.next_stage is SalesStage.OBJECTION_HANDLING


async def test_unknown_service_recovers_by_offering_the_catalogue(agent_settings, pricing: FakePricingGateway) -> None:
    conversation = Conversation(id="c1", stage=SalesStage.PRESENT, service="Hot Stone Facial", quantity=2)
    conversation.add_user("what's the price on that?")
    stage = PresentStage(StubLLM(control("We don't offer that one, but here's what we have.")), agent_settings, pricing)

    result = await stage.handle(conversation)

    assert conversation.quote is None
    assert conversation.offered_services  # catalogue fetched for the recovery
    assert result.next_stage is SalesStage.PRESENT  # stays until something prices
