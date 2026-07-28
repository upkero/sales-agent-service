from typing import ClassVar

from src.app.contracts.conversation import Conversation
from src.app.contracts.sales import SalesStage
from src.app.prompts import Prompt, get_prompt
from src.app.services.dialog.decision import AgentDecision
from src.app.services.dialog.stages.base import DialogueStage

_GREETING = get_prompt("stage_greeting")


class GreetingStage(DialogueStage):
    """Open the conversation, then always hand off to qualification.

    Greeting is a single beat: say hello and invite the prospect to talk. There is
    nothing to detect here, so the transition is unconditional — the simplest
    possible `route()`, and the reason greeting needs no `data`."""

    stage: ClassVar[SalesStage] = SalesStage.GREETING
    prompts: ClassVar[tuple[Prompt, ...]] = (_GREETING,)

    def directive(self, conversation: Conversation) -> str:
        return _GREETING.text

    def route(self, conversation: Conversation, decision: AgentDecision) -> SalesStage:
        # Move straight into qualification next turn; the greeting has done its one job.
        return SalesStage.QUALIFY
