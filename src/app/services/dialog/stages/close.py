from typing import ClassVar

from src.app.contracts.conversation import Conversation
from src.app.contracts.sales import SalesStage
from src.app.services.dialog.decision import AgentDecision
from src.app.services.dialog.stages.base import DialogueStage


class CloseStage(DialogueStage):
    """The terminal stage: wrap up and confirm next steps.

    Its `route()` always returns CLOSE — the machine has nowhere left to go, and
    making that explicit (rather than special-casing "no next stage" in the
    orchestrator) keeps the dispatch loop uniform."""

    stage: ClassVar[SalesStage] = SalesStage.CLOSE

    def directive(self, conversation: Conversation) -> str:
        return (
            "The deal is done or the customer is ready to decide. Thank them warmly, confirm the "
            "next concrete step (how to book or who will follow up), and close on a friendly note. "
            "Do not reopen the pitch."
        )

    def route(self, conversation: Conversation, decision: AgentDecision) -> SalesStage:
        return SalesStage.CLOSE
