from logging import getLogger
from typing import ClassVar

from src.app.contracts.conversation import Conversation
from src.app.contracts.sales import SalesStage
from src.app.core.settings.agent import SalesAgentSettings
from src.app.interfaces.llm.llm_client import LLMClient
from src.app.interfaces.pricing_gateway import PricingGateway
from src.app.prompts import Prompt, get_prompt
from src.app.services.dialog.decision import AgentDecision
from src.app.services.dialog.stages.base import DialogueStage
from src.app.services.sales.tactics import SalesTactic

logger = getLogger(__name__)

_UPSELL = get_prompt("stage_upsell")
_AFFIRM = get_prompt("stage_upsell_affirm")


class UpsellStage(DialogueStage):
    """Offer the prospect a bigger commitment that unlocks a better price.

    The stage decides *that* it upsells; the `SalesTactic` (Strategy) decides
    *which* quantity — here, the next volume tier ops-core-api rewards. The offer
    price is a second, live pricing call, so the number the prospect hears is the
    real discounted total, not an estimate. Two turns: make the offer, then close
    once they respond either way."""

    stage: ClassVar[SalesStage] = SalesStage.UPSELL
    prompts: ClassVar[tuple[Prompt, ...]] = (_UPSELL, _AFFIRM)
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
        if conversation.upsell_quote is not None or conversation.upsell_offered:
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
        if conversation.upsell_quote is None:
            return _AFFIRM.text
        return _UPSELL.render(
            upsell_facts=self._describe_quote(conversation.upsell_quote),
            current_facts=self._describe_quote(conversation.quote) if conversation.quote else "",
        )

    def data_spec(self) -> str:
        return '"accept": boolean'

    def absorb(self, conversation: Conversation, decision: AgentDecision) -> None:
        # Only the answer turn decides; on the offer turn nothing has been offered
        # yet, and a changed order goes back to PRESENT undecided.
        if not conversation.upsell_offered or conversation.quote_is_stale:
            return
        offer = conversation.upsell_quote
        # "Yes, six" is an acceptance even when the model forgets the flag: the
        # order-change step has already put the offered quantity in the slots.
        if offer is not None and (decision.flag("accept") or conversation.quantity == offer.quantity):
            conversation.quote = offer
            conversation.quantity = offer.quantity
            conversation.accepted_offer = "upsell"
        else:
            conversation.accepted_offer = "base"
        quote = conversation.quote
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
