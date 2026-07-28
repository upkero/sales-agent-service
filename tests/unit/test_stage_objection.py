from decimal import Decimal

from src.app.contracts.conversation import Conversation
from src.app.contracts.sales import SalesStage
from src.app.core.settings.agent import SalesAgentSettings
from src.app.services.dialog.stages.objection import ObjectionHandlingStage
from tests.fakes import StubLLM, compute_quote, control


def _objecting_conversation() -> Conversation:
    conversation = Conversation(id="c1", stage=SalesStage.OBJECTION_HANDLING, service="Deep Tissue Massage", quantity=3)
    conversation.quote = compute_quote("Deep Tissue Massage", Decimal("120.00"), 3)
    conversation.add_user("that feels a bit expensive")
    return conversation


async def test_advances_to_upsell_once_the_concern_is_resolved(agent_settings: SalesAgentSettings) -> None:
    llm = StubLLM(control("Totally fair — and buying more brings the price down.", resolved=True))
    stage = ObjectionHandlingStage(llm, agent_settings)
    conversation = _objecting_conversation()

    result = await stage.handle(conversation)

    assert result.next_stage is SalesStage.UPSELL


async def test_stays_while_the_concern_is_unresolved(agent_settings: SalesAgentSettings) -> None:
    llm = StubLLM(control("What part feels off — the price or the timing?", resolved=False))
    stage = ObjectionHandlingStage(llm, agent_settings)
    conversation = _objecting_conversation()

    result = await stage.handle(conversation)

    assert result.next_stage is SalesStage.OBJECTION_HANDLING
