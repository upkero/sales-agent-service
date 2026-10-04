from logging import getLogger
from typing import ClassVar

from src.app.contracts.conversation import Conversation
from src.app.contracts.llm.llm_message import LLMMessage
from src.app.contracts.sales import SalesStage
from src.app.core.settings.agent import SalesAgentSettings
from src.app.interfaces.llm.llm_client import LLMClient
from src.app.interfaces.pricing_gateway import PricingGateway
from src.app.messages import get_message
from src.app.prompts import Prompt, get_prompt
from src.app.services.dialog.amounts import states_amount
from src.app.services.dialog.decision import AgentDecision, extract_json_object
from src.app.services.dialog.stages.base import DialogueStage
from src.app.services.sales.tactics import SalesTactic

logger = getLogger(__name__)

_UPSELL = get_prompt("stage_upsell")
_AFFIRM = get_prompt("stage_upsell_affirm")
_ANSWER = get_prompt("stage_upsell_answer")
_TAKE = get_prompt("upsell_take")


class UpsellStage(DialogueStage):
    """Offer the prospect a bigger commitment that unlocks a better price.

    The stage decides *that* it upsells; the `SalesTactic` (Strategy) decides
    *which* quantity — here, the next volume tier ops-core-api rewards. The offer
    price is a second, live pricing call, so the number the prospect hears is the
    real discounted total, not an estimate. Two turns: make the offer, then read
    the answer and ask for a name and a contact, after which CLOSE confirms."""

    stage: ClassVar[SalesStage] = SalesStage.UPSELL
    prompts: ClassVar[tuple[Prompt, ...]] = (_UPSELL, _AFFIRM, _ANSWER, _TAKE)
    takes_order_changes: ClassVar[bool] = True

    def __init__(
        self,
        llm: LLMClient,
        settings: SalesAgentSettings,
        pricing: PricingGateway,
        tactic: SalesTactic,
    ) -> None:
        super().__init__(llm, settings)
        self._pricing = pricing
        self._tactic = tactic

    async def prepare(self, conversation: Conversation) -> None:
        if conversation.upsell_offered:
            # The answer turn. Whether they took the offer is read first, by a call
            # that sees only the offer and the reply: as one more field of the reply
            # call, a small model marked bare contact details as a yes.
            conversation.upsell_taken = await self._takes_offer(conversation)
            return
        if conversation.upsell_quote is not None:
            return
        if conversation.service is None or conversation.quantity is None:
            return
        target = self._tactic.upsell_target(conversation.quantity)
        if target is None:
            # Already at the top tier — nothing to upsell on volume. The directive
            # will pivot to affirming their choice instead.
            return
        conversation.upsell_quote = await self._pricing.quote(conversation.service, target)

    def directive(self, conversation: Conversation) -> str:
        if conversation.upsell_offered:
            # The answer turn: acknowledge the choice already read, and ask, once,
            # who to confirm it with.
            if conversation.upsell_taken and conversation.upsell_quote is not None:
                choice = f"They took the larger package: {self._describe_quote(conversation.upsell_quote)}"
            else:
                # Neutral on purpose: "they keep their order" talked the model out of
                # reporting "neither, make it 10" as the change it is.
                choice = "They did not take the larger package."
            return _ANSWER.render(choice=choice)
        if conversation.upsell_quote is None:
            return _AFFIRM.text
        return _UPSELL.render(
            upsell_facts=self._describe_quote(conversation.upsell_quote),
            current_facts=self._describe_quote(conversation.quote) if conversation.quote else "",
        )

    def ensure_stated(self, conversation: Conversation, reply: str) -> str:
        # The offer turn must make the offer: route() marks it offered, and the next
        # turn reads the answer to it. Seen live after "no thanks" or a settled
        # concern: a polite "anything else?" with no offer in it.
        offer = conversation.upsell_quote
        if conversation.upsell_offered or offer is None or states_amount(reply, offer.total):
            return reply
        sentence = get_message(conversation.language, "upsell_offer").format(
            quantity=offer.quantity, service=offer.service_name, total=offer.total
        )
        return f"{reply} {sentence}"

    async def _takes_offer(self, conversation: Conversation) -> bool:
        offer = conversation.upsell_quote
        if offer is None:
            return False  # top tier: there was nothing to take
        system = _TAKE.render(
            upsell_facts=self._describe_quote(offer),
            quote_facts=self._describe_quote(conversation.quote) if conversation.quote else "",
        )
        # The offer and the reply go in as the chat turns they are, not into the
        # system role: the reply is the customer's own words.
        exchange = [LLMMessage(role=message.role, content=message.content) for message in conversation.recent(2)]
        response = await self._llm.complete([LLMMessage(role="system", content=system), *exchange], json_mode=True)
        verdict = extract_json_object(response.content) or {}
        return verdict.get("take") is True

    def _absorb_order_change(self, conversation: Conversation, decision: AgentDecision) -> None:
        # On the offer turn the prospect has not heard the offer yet (their message
        # was already read for changes by the stage that handed over), so a quantity
        # in "data" is the model echoing its own offer. Taken as their order, it
        # would turn whatever they say next — their contact details — into a yes.
        if conversation.upsell_offered:
            super()._absorb_order_change(conversation, decision)

    def absorb(self, conversation: Conversation, decision: AgentDecision) -> None:
        # Only the answer turn decides; on the offer turn nothing has been offered
        # yet, and a changed order goes back to PRESENT undecided.
        if not conversation.upsell_offered or conversation.quote_is_stale:
            return
        offer = conversation.upsell_quote
        if offer is not None and conversation.upsell_taken:
            conversation.quote = offer
            conversation.accepted_offer = "upsell"
        else:
            conversation.accepted_offer = "base"
        quote = conversation.quote
        if quote is not None:
            # The order is the accepted quote, whatever the reply call echoed into
            # "data" (it tends to repeat the offered quantity either way).
            conversation.service = quote.service_name
            conversation.quantity = quote.quantity
        logger.info(
            "Offer accepted",
            extra={
                "conversation_id": conversation.id,
                "accepted_offer": conversation.accepted_offer,
                "service": quote.service_name if quote else None,
                "quantity": quote.quantity if quote else None,
                "total": str(quote.total) if quote else None,
            },
        )

    def route(self, conversation: Conversation, decision: AgentDecision) -> SalesStage:
        if conversation.quote_is_stale:
            # A quantity or service that neither the plan nor the offer prices:
            # back to PRESENT, which prices it before anything else is said.
            return SalesStage.PRESENT
        if not conversation.upsell_offered:
            # Offer (or affirmation) made this turn; wait for the prospect's answer.
            conversation.upsell_offered = True
            return SalesStage.UPSELL
        # They have responded — accept or decline, the sale moves to closing.
        return SalesStage.CLOSE
