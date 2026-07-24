from typing import ClassVar

from src.app.contracts.conversation import Conversation
from src.app.contracts.sales import SalesStage
from src.app.core.settings.agent import SalesAgentSettings
from src.app.exceptions.pricing import ServiceNotFoundError
from src.app.interfaces.llm.llm_client import LLMClient
from src.app.interfaces.pricing_gateway import PricingGateway
from src.app.services.dialog.decision import AgentDecision
from src.app.services.dialog.extraction import canonical_service, coerce_quantity
from src.app.services.dialog.stages.base import DialogueStage


class PresentStage(DialogueStage):
    """Fetch the live price from ops-core-api and put the offer in front of the
    prospect, then read their reaction.

    This is the stage that actually talks to the pricing service (Adapter). It
    spans two turns by design: present the number, then — once the prospect has
    reacted — branch. No objection means the sanctioned single skip straight to
    UPSELL, past objection handling.

    A price that unavailable (503) is left to propagate as a typed error; a
    service that simply is not in the catalogue (404) is recovered here, by
    offering the real catalogue and letting the prospect re-choose."""

    stage: ClassVar[SalesStage] = SalesStage.PRESENT

    def __init__(self, llm: LLMClient, settings: SalesAgentSettings, pricing: PricingGateway) -> None:
        super().__init__(llm, settings)
        self._pricing = pricing

    async def prepare(self, conversation: Conversation) -> None:
        if not self._needs_quote(conversation):
            return
        assert conversation.service is not None and conversation.quantity is not None  # noqa: S101
        try:
            conversation.quote = await self._pricing.quote(conversation.service, conversation.quantity)
            conversation.price_presented = False
        except ServiceNotFoundError:
            # Recoverable: the prospect named something not on the list. Drop the
            # stale quote and make sure we can show them what does exist.
            conversation.quote = None
            if not conversation.offered_services:
                conversation.offered_services = tuple(
                    item.service_name for item in await self._pricing.list_services()
                )

    def directive(self, conversation: Conversation) -> str:
        if conversation.quote is None:
            catalogue = ", ".join(conversation.offered_services) or "our listed services"
            return (
                f"We do not offer '{conversation.service}'. Apologise briefly and tell the customer "
                f"what we do offer: {catalogue}. Ask which of these they would like. "
                'In "data", set "service" to the exact catalogue name if they name one, else null.'
            )
        return (
            "Present this offer to the customer in a natural, confident sentence, stating the total "
            f"clearly. The facts: {self._describe_quote(conversation.quote)} "
            "Then invite their reaction. If they push back on price or value, that is an objection. "
            'In "data", set "objection" to true only if they actually raised a concern this turn.'
        )

    def data_spec(self) -> str:
        return '"objection": boolean, "service": string|null, "quantity": integer|null'

    def absorb(self, conversation: Conversation, decision: AgentDecision) -> None:
        # Correction path: if the prospect renamed the service or changed the count,
        # capture it so prepare() re-quotes next turn.
        service = decision.data.get("service")
        if isinstance(service, str) and service.strip():
            conversation.service = canonical_service(conversation.offered_services, service.strip())
        quantity = coerce_quantity(decision.data.get("quantity"))
        if quantity is not None:
            conversation.quantity = quantity

    def route(self, conversation: Conversation, decision: AgentDecision) -> SalesStage:
        if conversation.quote is None:
            # Nothing priced yet (unknown service). Stay until we can make an offer.
            return SalesStage.PRESENT
        if not conversation.price_presented:
            # We stated the price this turn; wait one turn for the prospect's reaction
            # before deciding whether it drew an objection.
            conversation.price_presented = True
            return SalesStage.PRESENT
        # Reaction is in. No objection -> skip objection handling straight to the upsell.
        return SalesStage.OBJECTION_HANDLING if decision.flag("objection") else SalesStage.UPSELL

    @staticmethod
    def _needs_quote(conversation: Conversation) -> bool:
        """Re-price only when there is no fresh quote for the current slots — so a
        second PRESENT turn does not make a redundant call, but a corrected service
        or quantity does trigger a new one."""
        if conversation.service is None or conversation.quantity is None:
            return False
        quote = conversation.quote
        return (
            quote is None
            or quote.service_name.lower() != conversation.service.lower()
            or quote.quantity != conversation.quantity
        )
