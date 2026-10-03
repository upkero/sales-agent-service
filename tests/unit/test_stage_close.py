from decimal import Decimal

from src.app.contracts.conversation import Conversation
from src.app.contracts.sales import SalesStage
from src.app.core.settings.agent import SalesAgentSettings
from src.app.services.dialog.stages.close import CloseStage
from tests.fakes import StubLLM, compute_quote, control


async def test_close_is_terminal(agent_settings: SalesAgentSettings) -> None:
    stage = CloseStage(StubLLM(control("Wonderful — I'll send the booking link now. Thank you!")), agent_settings)
    conversation = Conversation(id="c1", stage=SalesStage.CLOSE)
    conversation.add_user("great, thanks")

    result = await stage.handle(conversation)

    assert result.current_stage is SalesStage.CLOSE
    assert result.next_stage is SalesStage.CLOSE


async def test_close_is_told_the_accepted_order(agent_settings: SalesAgentSettings) -> None:
    llm = StubLLM(control("Thank you! Six sessions, 648.00 in total."))
    conversation = Conversation(id="c1", stage=SalesStage.CLOSE, service="Deep Tissue Massage", quantity=6)
    conversation.quote = compute_quote("Deep Tissue Massage", Decimal("120.00"), 6)
    conversation.accepted_offer = "upsell"
    conversation.add_user("yes, six")

    await CloseStage(llm, agent_settings).handle(conversation)

    system = llm.calls[-1][0].content
    assert "6 x Deep Tissue Massage" in system
    assert "648.00" in system
