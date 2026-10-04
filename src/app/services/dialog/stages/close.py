from logging import getLogger
from typing import ClassVar

from src.app.contracts.conversation import Conversation
from src.app.contracts.sales import SalesStage
from src.app.prompts import Prompt, get_prompt
from src.app.services.dialog.amounts import states_amount
from src.app.services.dialog.decision import AgentDecision
from src.app.services.dialog.stages.base import DialogueStage

logger = getLogger(__name__)

_CLOSE = get_prompt("stage_close")
_CLOSED = get_prompt("stage_closed")

# Plenty for a name, a phone number or an email; anything longer is not one.
_MAX_DETAIL_LENGTH = 120


class CloseStage(DialogueStage):
    """The terminal stage: confirm the order once, then the conversation is done.

    UPSELL's last reply asked for a name and a contact. The first CLOSE turn reads
    the answer, confirms the accepted order with its real total and says who will
    follow up, and marks the conversation closed. Anything said after that gets a
    brief answer that repeats nothing, which is what keeps the agent from saying
    "the front desk will confirm" on every turn.

    Its `route()` always returns CLOSE — the machine has nowhere left to go, and
    making that explicit (rather than special-casing "no next stage" in the
    orchestrator) keeps the dispatch loop uniform."""

    stage: ClassVar[SalesStage] = SalesStage.CLOSE
    prompts: ClassVar[tuple[Prompt, ...]] = (_CLOSE, _CLOSED)

    def directive(self, conversation: Conversation) -> str:
        if conversation.closed:
            return _CLOSED.text
        # The accepted order (see UpsellStage), so the confirmation names the
        # package and its real total instead of reconstructing them from the chat.
        order_facts = self._describe_quote(conversation.quote) if conversation.quote else ""
        return _CLOSE.render(order_facts=order_facts)

    def ensure_stated(self, conversation: Conversation, reply: str) -> str:
        # The confirmation names the order's total. Seen live: the model repeated its
        # previous "the front desk will confirm shortly" and confirmed nothing.
        quote = conversation.quote
        if conversation.closed or quote is None or states_amount(reply, quote.total):
            return reply
        return f"{reply} {self._total_sentence(conversation, quote)}"

    def data_spec(self) -> str:
        # Asked for after the close too, and ignored there (see absorb): data_spec
        # does not see the conversation, and an unused field costs nothing.
        return '"name": string|null, "contact": string|null'

    def absorb(self, conversation: Conversation, decision: AgentDecision) -> None:
        if conversation.closed:
            return
        conversation.customer_name = _detail(decision.data.get("name"))
        conversation.customer_contact = _detail(decision.data.get("contact"))
        conversation.closed = True
        quote = conversation.quote
        # The contact itself stays out of the log: it is personal data, and the
        # conversation already holds it for whatever acts on the order.
        logger.info(
            "Sale closed",
            extra={
                "conversation_id": conversation.id,
                "accepted_offer": conversation.accepted_offer,
                "service": quote.service_name if quote else None,
                "quantity": quote.quantity if quote else None,
                "total": str(quote.total) if quote else None,
                "contact_given": conversation.customer_contact is not None,
            },
        )

    def route(self, conversation: Conversation, decision: AgentDecision) -> SalesStage:
        return SalesStage.CLOSE


def _detail(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:_MAX_DETAIL_LENGTH]
