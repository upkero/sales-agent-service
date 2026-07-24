from typing import ClassVar

from src.app.contracts.conversation import Conversation
from src.app.contracts.sales import SalesStage
from src.app.services.dialog.decision import AgentDecision
from src.app.services.dialog.stages.base import DialogueStage


class GreetingStage(DialogueStage):
    """Open the conversation, then always hand off to qualification.

    Greeting is a single beat: say hello and invite the prospect to talk. There is
    nothing to detect here, so the transition is unconditional — the simplest
    possible `route()`, and the reason greeting needs no `data`."""

    stage: ClassVar[SalesStage] = SalesStage.GREETING

    def directive(self, conversation: Conversation) -> str:
        return (
            "This is the very start of the conversation. Greet the customer warmly, introduce "
            "yourself and the company in one line, and ask what brought them in or what they are "
            "looking for today. Do not pitch anything yet."
        )

    def route(self, conversation: Conversation, decision: AgentDecision) -> SalesStage:
        # Move straight into qualification next turn; the greeting has done its one job.
        return SalesStage.QUALIFY
