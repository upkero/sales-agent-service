from decimal import Decimal

from src.app.contracts.conversation import Conversation
from src.app.contracts.sales import SalesStage
from src.app.core.settings.agent import SalesAgentSettings
from src.app.services.dialog.stages.upsell import UpsellStage
from src.app.services.sales.tactics import VolumeDiscountTactic
from tests.fakes import FakePricingGateway, StubLLM, compute_quote, control


def _ready_conversation(quantity: int = 3) -> Conversation:
    conversation = Conversation(id="c1", stage=SalesStage.UPSELL, service="Deep Tissue Massage", quantity=quantity)
    conversation.quote = compute_quote("Deep Tissue Massage", Decimal("120.00"), quantity)
    conversation.add_user("okay, I'm interested")
    return conversation


async def test_offers_the_next_tier_at_a_live_discounted_price(
    agent_settings: SalesAgentSettings,
    pricing: FakePricingGateway,
) -> None:
    stage = UpsellStage(
        StubLLM(control("Book six and you'll save 10% — 648 instead of 720.", accept=True)),
        agent_settings,
        pricing,
        VolumeDiscountTactic(),
    )
    conversation = _ready_conversation(quantity=3)

    result = await stage.handle(conversation)

    # The upsell quantity (6) comes from the Strategy; the price is a real second
    # call to the pricing gateway, not an estimate.
    assert ("quote", "Deep Tissue Massage", "6") in pricing.calls
    assert conversation.upsell_quote is not None
    assert conversation.upsell_quote.quantity == 6
    assert conversation.upsell_quote.total == Decimal("648.00")  # 6 x 120 = 720, less 10%
    assert conversation.upsell_offered is True
    assert result.next_stage is SalesStage.UPSELL  # offered this turn, awaits the answer


async def test_closes_after_the_prospect_responds_to_the_offer(
    agent_settings: SalesAgentSettings,
    pricing: FakePricingGateway,
) -> None:
    conversation = _ready_conversation(quantity=3)
    conversation.upsell_quote = compute_quote("Deep Tissue Massage", Decimal("120.00"), 6)
    conversation.upsell_offered = True  # the offer was made last turn
    llm = StubLLM(control("Perfect, I'll set up six.", accept=True))
    stage = UpsellStage(llm, agent_settings, pricing, VolumeDiscountTactic())

    result = await stage.handle(conversation)

    assert result.next_stage is SalesStage.CLOSE


def _answering_conversation() -> Conversation:
    conversation = _ready_conversation(quantity=3)
    conversation.upsell_quote = compute_quote("Deep Tissue Massage", Decimal("120.00"), 6)
    conversation.upsell_offered = True
    return conversation


async def test_an_accepted_upsell_becomes_the_order(
    agent_settings: SalesAgentSettings,
    pricing: FakePricingGateway,
) -> None:
    conversation = _answering_conversation()
    stage = UpsellStage(StubLLM(control("Six it is.", accept=True)), agent_settings, pricing, VolumeDiscountTactic())

    await stage.handle(conversation)

    assert conversation.accepted_offer == "upsell"
    assert conversation.quantity == 6
    assert conversation.quote is not None
    assert conversation.quote.total == Decimal("648.00")


async def test_naming_the_offered_quantity_accepts_it_even_without_the_flag(
    agent_settings: SalesAgentSettings,
    pricing: FakePricingGateway,
) -> None:
    conversation = _answering_conversation()
    stage = UpsellStage(StubLLM(control("Six it is.", quantity=6)), agent_settings, pricing, VolumeDiscountTactic())

    await stage.handle(conversation)

    assert conversation.accepted_offer == "upsell"
    assert conversation.quote is not None and conversation.quote.quantity == 6


async def test_a_declined_upsell_keeps_the_base_offer(
    agent_settings: SalesAgentSettings,
    pricing: FakePricingGateway,
) -> None:
    conversation = _answering_conversation()
    stage = UpsellStage(StubLLM(control("Three it is.", accept=False)), agent_settings, pricing, VolumeDiscountTactic())

    await stage.handle(conversation)

    assert conversation.accepted_offer == "base"
    assert conversation.quantity == 3
    assert conversation.quote is not None and conversation.quote.total == Decimal("360.00")


async def test_the_offer_turn_records_no_answer(
    agent_settings: SalesAgentSettings,
    pricing: FakePricingGateway,
) -> None:
    # accept=True on the turn the offer is made is the model jumping ahead.
    llm = StubLLM(control("Six for 648.00?", accept=True))
    stage = UpsellStage(llm, agent_settings, pricing, VolumeDiscountTactic())
    conversation = _ready_conversation(quantity=3)

    await stage.handle(conversation)

    assert conversation.accepted_offer is None
    assert conversation.quantity == 3


async def test_at_the_top_tier_there_is_nothing_to_upsell(
    agent_settings: SalesAgentSettings,
    pricing: FakePricingGateway,
) -> None:
    stage = UpsellStage(
        StubLLM(control("You're already getting our best rate — shall we book it?")),
        agent_settings,
        pricing,
        VolumeDiscountTactic(),
    )
    conversation = _ready_conversation(quantity=25)  # above the top discount tier

    result = await stage.handle(conversation)

    assert conversation.upsell_quote is None  # no bigger tier to pitch
    assert ("quote", "Deep Tissue Massage", "6") not in pricing.calls
    assert result.next_stage is SalesStage.UPSELL
