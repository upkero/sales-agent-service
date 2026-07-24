from src.app.contracts.conversation import Conversation
from src.app.contracts.sales import SalesStage
from src.app.services.dialog.stages.greeting import GreetingStage
from tests.fakes import StubLLM, control


async def test_greeting_replies_and_always_moves_to_qualify(agent_settings) -> None:
    stage = GreetingStage(StubLLM(control("Hi, I'm Alex from Aurora Wellness — what can I help with?")), agent_settings)
    conversation = Conversation(id="c1")
    conversation.add_user("hello")

    result = await stage.handle(conversation)

    assert result.current_stage is SalesStage.GREETING
    assert result.next_stage is SalesStage.QUALIFY
    assert "Alex" in result.reply
    assert result.handoff is False
