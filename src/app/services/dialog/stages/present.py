from typing import ClassVar

from src.app.contracts.conversation import Conversation
from src.app.contracts.sales import SalesStage
from src.app.core.settings.agent import SalesAgentSettings
from src.app.exceptions.pricing import ServiceNotFoundError
from src.app.interfaces.llm.llm_client import LLMClient
from src.app.interfaces.pricing_gateway import PricingGateway
from src.app.prompts import Prompt, get_prompt
from src.app.services.dialog.decision import AgentDecision
from src.app.services.dialog.stages.base import DialogueStage

_PRESENT = get_prompt("stage_present")
_UNKNOWN_SERVICE = get_prompt("stage_present_unknown_service")


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
    prompts: ClassVar[tuple[Prompt, ...]] = (_PRESENT, _UNKNOWN_SERVICE)
    takes_order_changes: ClassVar[bool] = True

    def __init__(self, llm: LLMClient, settings: SalesAgentSettings, pricing: PricingGateway) -> None:
        super().__init__(llm, settings)
        self._pricing = pricing

    async def prepare(self, conversation: Conversation) -> None:
        slots = self._slots_to_quote(conversation)
        if slots is None:
            return
        service, quantity = slots
        try:
            conversation.quote = await self._pricing.quote(service, quantity)
            conversation.price_presented = False
            # An upsell made from the previous order no longer fits this one;
            # the funnel offers it again from the new quote.
            conversation.upsell_quote = None
            conversation.upsell_offered = False
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
            return _UNKNOWN_SERVICE.render(
                requested_service=conversation.service,
                catalogue=", ".join(conversation.offered_services) or "our listed services",
            )
        return _PRESENT.render(quote_facts=self._describe_quote(conversation.quote))

    def data_spec(self) -> str:
        return '"objection": boolean'

    def route(self, conversation: Conversation, decision: AgentDecision) -> SalesStage:
        if conversation.quote is None:
            # Nothing priced yet (unknown service). Stay until we can make an offer.
            return SalesStage.PRESENT
        if self._slots_to_quote(conversation) is not None:
            # They changed the order. Stay, so it is priced before anything moves on;
            # the orchestrator runs this stage again in the same turn to do it.
            return SalesStage.PRESENT
        if not conversation.price_presented:
            # We stated the price this turn; wait one turn for the prospect's reaction
            # before deciding whether it drew an objection.
            conversation.price_presented = True
            return SalesStage.PRESENT
        # Reaction is in. No objection -> skip objection handling straight to the upsell.
        return SalesStage.OBJECTION_HANDLING if decision.flag("objection") else SalesStage.UPSELL

    @staticmethod
    def _slots_to_quote(conversation: Conversation) -> tuple[str, int] | None:
        """The (service, quantity) that still need pricing, or None when nothing
        does — so a second PRESENT turn makes no redundant call, but a corrected
        service or quantity does trigger a new one.

        It hands the slots back rather than answering yes/no on purpose: the
        caller then holds values mypy already knows are not None, instead of an
        `assert` restating a condition this method has just checked. Narrow the
        type, do not raise — an assert on the request path disappears under
        `python -O` and, on the one turn it did fire, would escape as a bare 500.
        """
        service, quantity = conversation.service, conversation.quantity
        if service is None or quantity is None:
            return None
        quote = conversation.quote
        if quote is None or quote.service_name.lower() != service.lower() or quote.quantity != quantity:
            return service, quantity
        return None
