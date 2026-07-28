from typing import ClassVar

from src.app.contracts.conversation import Conversation
from src.app.contracts.sales import SalesStage
from src.app.prompts import Prompt, get_prompt
from src.app.services.dialog.decision import AgentDecision
from src.app.services.dialog.stages.base import DialogueStage

_OBJECTION = get_prompt("stage_objection")


class ObjectionHandlingStage(DialogueStage):
    """Address whatever concern the prospect raised about the offer, then move on
    once it is settled.

    Needs no pricing call of its own: it reasons about the quote PRESENT already
    fetched, using the volume discount as the lever. It advances to UPSELL only
    when the model reports the concern resolved — a data-driven transition, so a
    single reassuring sentence does not get mistaken for agreement."""

    stage: ClassVar[SalesStage] = SalesStage.OBJECTION_HANDLING
    prompts: ClassVar[tuple[Prompt, ...]] = (_OBJECTION,)

    def directive(self, conversation: Conversation) -> str:
        quote_facts = self._describe_quote(conversation.quote) if conversation.quote else ""
        return _OBJECTION.render(quote_facts=quote_facts)

    def data_spec(self) -> str:
        return '"resolved": boolean'

    def route(self, conversation: Conversation, decision: AgentDecision) -> SalesStage:
        return SalesStage.UPSELL if decision.flag("resolved") else SalesStage.OBJECTION_HANDLING
