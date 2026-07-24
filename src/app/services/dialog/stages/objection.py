from typing import ClassVar

from src.app.contracts.conversation import Conversation
from src.app.contracts.sales import SalesStage
from src.app.services.dialog.decision import AgentDecision
from src.app.services.dialog.stages.base import DialogueStage


class ObjectionHandlingStage(DialogueStage):
    """Address whatever concern the prospect raised about the offer, then move on
    once it is settled.

    Needs no pricing call of its own: it reasons about the quote PRESENT already
    fetched, using the volume discount as the lever. It advances to UPSELL only
    when the model reports the concern resolved — a data-driven transition, so a
    single reassuring sentence does not get mistaken for agreement."""

    stage: ClassVar[SalesStage] = SalesStage.OBJECTION_HANDLING

    def directive(self, conversation: Conversation) -> str:
        quote_facts = self._describe_quote(conversation.quote) if conversation.quote else ""
        return (
            "The customer has a concern about the offer. Acknowledge it genuinely and answer it in "
            "one or two sentences. If it is about price, remind them that buying more sessions unlocks "
            f"a volume discount. The current offer: {quote_facts} "
            'In "data", set "resolved" to true only once the customer seems satisfied or ready to move on.'
        )

    def data_spec(self) -> str:
        return '"resolved": boolean'

    def route(self, conversation: Conversation, decision: AgentDecision) -> SalesStage:
        return SalesStage.UPSELL if decision.flag("resolved") else SalesStage.OBJECTION_HANDLING
