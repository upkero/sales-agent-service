from src.app.contracts.conversation import Conversation
from src.app.contracts.sales import SalesStage
from src.app.services.dialog.stages.close import CloseStage
from tests.fakes import StubLLM, control


async def test_close_is_terminal(agent_settings) -> None:
    stage = CloseStage(StubLLM(control("Wonderful — I'll send the booking link now. Thank you!")), agent_settings)
    conversation = Conversation(id="c1", stage=SalesStage.CLOSE)
    conversation.add_user("great, thanks")

    result = await stage.handle(conversation)

    assert result.current_stage is SalesStage.CLOSE
    assert result.next_stage is SalesStage.CLOSE
