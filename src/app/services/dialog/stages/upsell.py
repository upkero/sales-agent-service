from typing import ClassVar

from src.app.contracts.conversation import Conversation
from src.app.contracts.sales import SalesStage
from src.app.core.settings.agent import SalesAgentSettings
from src.app.interfaces.llm.llm_client import LLMClient
from src.app.interfaces.pricing_gateway import PricingGateway
from src.app.services.dialog.decision import AgentDecision
from src.app.services.dialog.stages.base import DialogueStage
from src.app.services.sales.tactics import SalesTactic


class UpsellStage(DialogueStage):
    """Offer the prospect a bigger commitment that unlocks a better price.

    The stage decides *that* it upsells; the `SalesTactic` (Strategy) decides
    *which* quantity — here, the next volume tier ops-core-api rewards. The offer
    price is a second, live pricing call, so the number the prospect hears is the
    real discounted total, not an estimate. Two turns: make the offer, then close
    once they respond either way."""

    stage: ClassVar[SalesStage] = SalesStage.UPSELL

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
            return (
                "The customer is happy with their choice and there is no larger package worth "
                "suggesting. Warmly affirm their decision and encourage them to go ahead and book."
            )
        current = self._describe_quote(conversation.quote) if conversation.quote else ""
        upsell = self._describe_quote(conversation.upsell_quote)
        return (
            "The customer is on board. Offer them a better-value option in one natural, upbeat "
            f"sentence: {upsell} Contrast it with their current plan ({current}), pointing out the "
            "lower effective price per session. Invite them to take the larger package, without "
            'pressure. In "data", set "accept" to true if they agree to it.'
        )

    def data_spec(self) -> str:
        return '"accept": boolean'

    def route(self, conversation: Conversation, decision: AgentDecision) -> SalesStage:
        if not conversation.upsell_offered:
            # Offer (or affirmation) made this turn; wait for the prospect's answer.
            conversation.upsell_offered = True
            return SalesStage.UPSELL
        # They have responded — accept or decline, the sale moves to closing.
        return SalesStage.CLOSE
