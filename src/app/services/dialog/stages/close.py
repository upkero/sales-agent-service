from logging import getLogger
from typing import ClassVar

from src.app.contracts.conversation import Conversation
from src.app.contracts.lead import Lead
from src.app.contracts.sales import SalesStage
from src.app.core.settings.agent import SalesAgentSettings
from src.app.exceptions.lead import LeadNotSavedError
from src.app.interfaces.lead_gateway import LeadGateway
from src.app.interfaces.llm.llm_client import LLMClient
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
    """The terminal stage: take the request once, then the conversation is done.

    UPSELL's last reply asked for a name and a contact. The first CLOSE turn reads
    the answer, hands the lead to ops-core-api for the front desk, tells the customer
    the request is in with its real total and that the front desk will contact them
    to pick a time, and marks the conversation closed. Nothing is booked or paid here,
    and the reply never says so. Anything said after that gets a brief answer that
    repeats nothing, which is what keeps the agent from promising a call on every turn.

    Its `route()` always returns CLOSE — the machine has nowhere left to go, and
    making that explicit (rather than special-casing "no next stage" in the
    orchestrator) keeps the dispatch loop uniform."""

    stage: ClassVar[SalesStage] = SalesStage.CLOSE
    prompts: ClassVar[tuple[Prompt, ...]] = (_CLOSE, _CLOSED)

    def __init__(self, llm: LLMClient, settings: SalesAgentSettings, leads: LeadGateway) -> None:
        super().__init__(llm, settings)
        self._leads = leads

    def directive(self, conversation: Conversation) -> str:
        if conversation.closed:
            return _CLOSED.text
        # The accepted order (see UpsellStage), so the confirmation names the
        # package and its real total instead of reconstructing them from the chat.
        order_facts = self._describe_quote(conversation.quote) if conversation.quote else ""
        return _CLOSE.render(order_facts=order_facts)

    def ensure_stated(self, conversation: Conversation, reply: str) -> str:
        # The reply names the order's total. Seen live: the model repeated its
        # previous "the front desk will confirm shortly" and said nothing about the order.
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
        # The name and contact stay out of the log: they are personal data, and ops-core-api
        # receives them (see commit).
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

    async def commit(self, conversation: Conversation) -> None:
        """Hand the lead to ops-core-api, once.

        The conversation closes whether or not this works: the customer has already been
        told their request is in, and a failed save is an alert for the operator, not a
        reason to leave them hanging. Nothing personal goes in the log line.
        """
        if not conversation.closed or conversation.lead_sent:
            return
        conversation.lead_sent = True
        quote = conversation.quote
        lead = Lead(
            conversation_id=conversation.id,
            name=conversation.customer_name,
            contact=conversation.customer_contact,
            service=quote.service_name if quote else conversation.service,
            quantity=quote.quantity if quote else conversation.quantity,
            total=quote.total if quote else None,
        )
        try:
            await self._leads.save_lead(lead)
        except LeadNotSavedError as exc:
            logger.error(
                "Lead not saved",
                extra={
                    "conversation_id": conversation.id,
                    "error_code": exc.error_code,
                    "service": lead.service,
                    "quantity": lead.quantity,
                    "total": str(lead.total) if lead.total is not None else None,
                    "contact_given": lead.contact is not None,
                },
            )
            return
        logger.info("Lead saved", extra={"conversation_id": conversation.id})

    def route(self, conversation: Conversation, decision: AgentDecision) -> SalesStage:
        return SalesStage.CLOSE


def _detail(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:_MAX_DETAIL_LENGTH]
