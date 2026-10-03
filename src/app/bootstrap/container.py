"""Manual dependency-injection container.

`cached_property` gives lazy singletons without a framework: nothing is built
until something asks for it, and asking twice returns the same object. This is
also the single place where the concrete implementations are chosen — the LLM
client, the HTTP pricing adapter, the in-memory store, the selling tactic. Every
layer above depends on the ports; only this file names the classes, so swapping
any one of them is a change here and nowhere else.
"""

import inspect
from collections.abc import Mapping
from functools import cached_property

from src.app.contracts.sales import SalesStage
from src.app.core.settings.agent import get_agent_settings
from src.app.core.settings.conversation import get_conversation_store_settings
from src.app.core.settings.core_api import get_core_api_settings
from src.app.core.settings.llm import get_llm_settings
from src.app.gateways.core_api_pricing import create_pricing_gateway
from src.app.interfaces.conversation_repository import ConversationRepository
from src.app.interfaces.llm.llm_client import LLMClient
from src.app.interfaces.pricing_gateway import PricingGateway
from src.app.llm.factory import create_llm_client
from src.app.repositories.memory_conversation import InMemoryConversationRepository
from src.app.services.dialog.stages.base import DialogueStage
from src.app.services.dialog.stages.close import CloseStage
from src.app.services.dialog.stages.greeting import GreetingStage
from src.app.services.dialog.stages.objection import ObjectionHandlingStage
from src.app.services.dialog.stages.present import PresentStage
from src.app.services.dialog.stages.qualify import QualifyStage
from src.app.services.dialog.stages.upsell import UpsellStage
from src.app.services.sales.service import SalesService
from src.app.services.sales.tactics import SalesTactic, VolumeDiscountTactic


class ApplicationContainer:
    @cached_property
    def llm_client(self) -> LLMClient:
        return create_llm_client(get_llm_settings())

    @cached_property
    def pricing_gateway(self) -> PricingGateway:
        return create_pricing_gateway(get_core_api_settings())

    @cached_property
    def conversation_repository(self) -> ConversationRepository:
        settings = get_conversation_store_settings()
        return InMemoryConversationRepository(
            ttl_seconds=settings.ttl_seconds,
            max_entries=settings.max_entries,
        )

    @cached_property
    def sales_tactic(self) -> SalesTactic:
        # The Strategy is chosen here and nowhere else. Swapping VolumeDiscountTactic
        # for a premium cross-sell is this one line; UpsellStage never learns of it.
        return VolumeDiscountTactic()

    @cached_property
    def stages(self) -> Mapping[SalesStage, DialogueStage]:
        """The stage registry the orchestrator dispatches through. This mapping is
        the whole state machine's wiring — every concrete stage constructed with
        exactly the collaborators it needs."""
        llm = self.llm_client
        settings = get_agent_settings()
        pricing = self.pricing_gateway
        return {
            SalesStage.GREETING: GreetingStage(llm, settings),
            SalesStage.QUALIFY: QualifyStage(llm, settings, pricing),
            SalesStage.PRESENT: PresentStage(llm, settings, pricing),
            SalesStage.OBJECTION_HANDLING: ObjectionHandlingStage(llm, settings),
            SalesStage.UPSELL: UpsellStage(llm, settings, pricing, self.sales_tactic),
            SalesStage.CLOSE: CloseStage(llm, settings),
        }

    @cached_property
    def sales_service(self) -> SalesService:
        return SalesService(
            self.conversation_repository,
            self.stages,
            default_language=get_agent_settings().language,
        )

    async def close(self) -> None:
        """Close whatever was actually built, once each.

        Duck-typed rather than a registry of closers: a dependency that owns a
        resource (the httpx client, the LLM SDK client) already knows how to
        release it, and a list of what to close is one more thing to forget to
        update. The stages hold references to those same objects but have no
        close() of their own, so they are simply skipped.
        """
        closed: set[int] = set()
        for dependency in tuple(self.__dict__.values()):
            if id(dependency) in closed:
                continue
            close = getattr(dependency, "close", None)
            if callable(close):
                result = close()
                if inspect.isawaitable(result):
                    await result
            closed.add(id(dependency))
