from typing import ClassVar

from src.app.contracts.conversation import Conversation
from src.app.contracts.sales import SalesStage
from src.app.prompts import Prompt, get_prompt
from src.app.services.dialog.decision import AgentDecision
from src.app.services.dialog.stages.base import DialogueStage

_CLOSE = get_prompt("stage_close")


class CloseStage(DialogueStage):
    """The terminal stage: wrap up and confirm next steps.

    Its `route()` always returns CLOSE — the machine has nowhere left to go, and
    making that explicit (rather than special-casing "no next stage" in the
    orchestrator) keeps the dispatch loop uniform."""

    stage: ClassVar[SalesStage] = SalesStage.CLOSE
    prompts: ClassVar[tuple[Prompt, ...]] = (_CLOSE,)

    def directive(self, conversation: Conversation) -> str:
        # The accepted order (see UpsellStage), so the confirmation names the
        # package and its real total instead of reconstructing them from the chat.
        order_facts = self._describe_quote(conversation.quote) if conversation.quote else ""
        return _CLOSE.render(order_facts=order_facts)

    def route(self, conversation: Conversation, decision: AgentDecision) -> SalesStage:
        return SalesStage.CLOSE
