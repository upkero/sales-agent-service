from logging import getLogger
from typing import ClassVar

from src.app.contracts.conversation import Conversation
from src.app.contracts.sales import SalesStage
from src.app.core.metrics import record_guardrail
from src.app.core.settings.agent import SalesAgentSettings
from src.app.exceptions.pricing import PricingError
from src.app.interfaces.llm.llm_client import LLMClient
from src.app.interfaces.pricing_gateway import PricingGateway
from src.app.prompts import Prompt, get_prompt
from src.app.services.dialog.decision import AgentDecision
from src.app.services.dialog.extraction import canonical_service, coerce_quantity
from src.app.services.dialog.stages.base import ORDER_FIELDS, DialogueStage

logger = getLogger(__name__)

_QUALIFY = get_prompt("stage_qualify")
_CATALOGUE = get_prompt("qualify_catalogue")
_KNOWN_SLOTS = get_prompt("qualify_known_slots")
_KNOWN_SERVICE = get_prompt("qualify_known_service")
_KNOWN_QUANTITY = get_prompt("qualify_known_quantity")
_MAX_QUANTITY_ASKS = 2


class QualifyStage(DialogueStage):
    """Find out which service the prospect wants and how many, filling the two
    slots PRESENT needs to fetch a price.

    Grounded on the real catalogue (fetched once) so the model extracts a service
    name that will actually price, rather than an approximate one that 404s. The
    transition is data-driven, not a raw model flag: it advances only once both
    slots are genuinely filled — the model cannot talk the machine forward."""

    stage: ClassVar[SalesStage] = SalesStage.QUALIFY
    prompts: ClassVar[tuple[Prompt, ...]] = (_QUALIFY, _CATALOGUE, _KNOWN_SLOTS, _KNOWN_SERVICE, _KNOWN_QUANTITY)

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
            record_guardrail("catalogue_unavailable", self.stage.value)
            return
        conversation.offered_services = tuple(catalogue)

    def directive(self, conversation: Conversation) -> str:
        # The trailing space joins each fragment to the sentence after it in stage_qualify.md.
        catalogue = (
            _CATALOGUE.render(services=", ".join(conversation.offered_services)) + " "
            if conversation.offered_services
            else ""
        )
        return _QUALIFY.render(catalogue=catalogue, known_slots=self._known_slots(conversation))

    def data_spec(self) -> str:
        return ORDER_FIELDS

    def absorb(self, conversation: Conversation, decision: AgentDecision) -> None:
        service = decision.data.get("service")
        if isinstance(service, str) and service.strip():
            conversation.service = canonical_service(conversation.offered_services, service.strip())

        quantity = coerce_quantity(decision.data.get("quantity"))
        if quantity is not None:
            conversation.quantity = quantity
        elif conversation.service is not None:
            # A prospect who asks the price, or says "book me in", without a number
            # was otherwise asked "how many sessions?" on every turn, forever.
            conversation.quantity_asks += 1
            if conversation.quantity_asks >= _MAX_QUANTITY_ASKS:
                conversation.quantity = 1
                record_guardrail("quantity_defaulted", self.stage.value)

    def route(self, conversation: Conversation, decision: AgentDecision) -> SalesStage:
        # Deterministic: advance only when both slots are actually filled.
        return SalesStage.PRESENT if conversation.is_qualified else SalesStage.QUALIFY

    @staticmethod
    def _known_slots(conversation: Conversation) -> str:
        known: list[str] = []
        # Only a catalogue name: anything else is the customer's own wording, which
        # stays in the user role (the history) rather than the system prompt.
        if conversation.service in conversation.offered_services:
            known.append(_KNOWN_SERVICE.render(service=conversation.service))
        if conversation.quantity is not None:
            known.append(_KNOWN_QUANTITY.render(quantity=conversation.quantity))
        return _KNOWN_SLOTS.render(known=", ".join(known)) + " " if known else ""
