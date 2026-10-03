from src.app.contracts.conversation import Conversation
from src.app.contracts.sales import SalesStage
from src.app.core.settings.agent import SalesAgentSettings
from src.app.messages import get_message
from src.app.services.dialog.stages.greeting import GreetingStage
from tests.fakes import StubLLM, control


async def test_greeting_replies_and_always_moves_to_qualify(agent_settings: SalesAgentSettings) -> None:
    stage = GreetingStage(StubLLM(control("Hi, I'm Alex from Aurora Wellness — what can I help with?")), agent_settings)
    conversation = Conversation(id="c1")
    conversation.add_user("hello")

    result = await stage.handle(conversation)

    assert result.current_stage is SalesStage.GREETING
    assert result.next_stage is SalesStage.QUALIFY
    assert "Alex" in result.reply
    assert result.handoff is False


async def test_a_price_before_any_quote_is_replaced_by_the_promise_to_check(
    agent_settings: SalesAgentSettings,
) -> None:
    stage = GreetingStage(StubLLM(control("Hi! Massages are 120.00 each.")), agent_settings)
    conversation = Conversation(id="c1")
    conversation.add_user("hi, how much is a massage?")

    result = await stage.handle(conversation)

    assert "120" not in result.reply
    assert result.reply == get_message("en", "price_pending")
