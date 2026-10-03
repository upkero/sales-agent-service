from logging import getLogger
from typing import ClassVar

from src.app.contracts.conversation import Conversation
from src.app.contracts.sales import SalesStage
from src.app.core.settings.agent import SalesAgentSettings
from src.app.exceptions.pricing import PricingError
from src.app.interfaces.llm.llm_client import LLMClient
from src.app.interfaces.pricing_gateway import PricingGateway
from src.app.prompts import Prompt, get_prompt
from src.app.services.dialog.decision import AgentDecision
from src.app.services.dialog.extraction import canonical_service, coerce_quantity
from src.app.services.dialog.stages.base import DialogueStage

logger = getLogger(__name__)

_QUALIFY = get_prompt("stage_qualify")


class QualifyStage(DialogueStage):
    """Find out which service the prospect wants and how many, filling the two
    slots PRESENT needs to fetch a price.

    Grounded on the real catalogue (fetched once) so the model extracts a service
    name that will actually price, rather than an approximate one that 404s. The
    transition is data-driven, not a raw model flag: it advances only once both
    slots are genuinely filled — the model cannot talk the machine forward."""

    stage: ClassVar[SalesStage] = SalesStage.QUALIFY
    prompts: ClassVar[tuple[Prompt, ...]] = (_QUALIFY,)

    def __init__(self, llm: LLMClient, settings: SalesAgentSettings, pricing: PricingGateway) -> None:
        super().__init__(llm, settings)
        self._pricing = pricing

    async def prepare(self, conversation: Conversation) -> None:
        if conversation.offered_services:
            return
        try:
            catalogue = await self._pricing.list_services()
        except PricingError as exc:
            # Grounding is a nice-to-have; a momentary pricing outage should not
            # block qualifying a prospect. Proceed without the catalogue.
            logger.warning("Catalogue unavailable for grounding: %s", exc, extra={"conversation_id": conversation.id})
            return
        conversation.offered_services = tuple(item.service_name for item in catalogue)

    def directive(self, conversation: Conversation) -> str:
        catalogue = (
            f"Our services are: {', '.join(conversation.offered_services)}. "
            if conversation.offered_services
            else ""
        )
        return _QUALIFY.render(catalogue=catalogue, known_slots=self._known_slots(conversation))

    def data_spec(self) -> str:
        return '"service": string|null, "quantity": integer|null'

    def absorb(self, conversation: Conversation, decision: AgentDecision) -> None:
        service = decision.data.get("service")
        if isinstance(service, str) and service.strip():
            conversation.service = canonical_service(conversation.offered_services, service.strip())

        quantity = coerce_quantity(decision.data.get("quantity"))
        if quantity is not None:
            conversation.quantity = quantity

    def route(self, conversation: Conversation, decision: AgentDecision) -> SalesStage:
        # Deterministic: advance only when both slots are actually filled.
        return SalesStage.PRESENT if conversation.is_qualified else SalesStage.QUALIFY

    @staticmethod
    def _known_slots(conversation: Conversation) -> str:
        known: list[str] = []
        # Only a catalogue name: anything else is the customer's own wording, which
        # stays in the user role (the history) rather than the system prompt.
        if conversation.service in conversation.offered_services:
            known.append(f"they want {conversation.service}")
        if conversation.quantity is not None:
            known.append(f"quantity {conversation.quantity}")
        return f"So far you know: {', '.join(known)}. " if known else ""
