from src.app.contracts.conversation import Conversation
from src.app.contracts.sales import SalesStage
from src.app.core.settings.agent import SalesAgentSettings
from src.app.services.dialog.stages.qualify import QualifyStage
from tests.fakes import FakePricingGateway, StubLLM, UnavailablePricingGateway, control


def _qualifying_conversation() -> Conversation:
    conversation = Conversation(id="c1", stage=SalesStage.QUALIFY)
    conversation.add_user("I'd like a few deep tissue massages")
    return conversation


async def test_advances_to_present_once_both_slots_are_filled(
    agent_settings: SalesAgentSettings,
    pricing: FakePricingGateway,
) -> None:
    stage = QualifyStage(
        StubLLM(control("Great — three deep tissue massages.", service="Deep Tissue Massage", quantity=3)),
        agent_settings,
        pricing,
    )
    conversation = _qualifying_conversation()

    result = await stage.handle(conversation)

    assert conversation.service == "Deep Tissue Massage"
    assert conversation.quantity == 3
    assert result.next_stage is SalesStage.PRESENT
    assert ("list_services",) in pricing.calls  # grounded on the real catalogue


async def test_stays_in_qualify_until_quantity_is_known(
    agent_settings: SalesAgentSettings,
    pricing: FakePricingGateway,
) -> None:
    stage = QualifyStage(
        StubLLM(control("Which service would you like?", service="Deep Tissue Massage")),
        agent_settings,
        pricing,
    )
    conversation = _qualifying_conversation()

    result = await stage.handle(conversation)

    assert conversation.quantity is None
    assert result.next_stage is SalesStage.QUALIFY


async def test_snaps_a_loose_name_onto_the_exact_catalogue_name(
    agent_settings: SalesAgentSettings,
    pricing: FakePricingGateway,
) -> None:
    stage = QualifyStage(
        StubLLM(control("Got it.", service="deep tissue massage", quantity=2)),
        agent_settings,
        pricing,
    )
    conversation = _qualifying_conversation()

    await stage.handle(conversation)

    # The whole-string price lookup is case-insensitive but not fuzzy, so the slot
    # is normalised to what will actually price.
    assert conversation.service == "Deep Tissue Massage"


async def test_grounding_degrades_when_pricing_is_down(agent_settings: SalesAgentSettings) -> None:
    stage = QualifyStage(
        StubLLM(control("Sure, what would you like?", service="Deep Tissue Massage", quantity=2)),
        agent_settings,
        UnavailablePricingGateway(),
    )
    conversation = _qualifying_conversation()

    # A pricing outage must not block qualifying a prospect: the stage proceeds
    # without the catalogue rather than raising.
    result = await stage.handle(conversation)

    assert conversation.offered_services == ()
    assert result.next_stage is SalesStage.PRESENT
