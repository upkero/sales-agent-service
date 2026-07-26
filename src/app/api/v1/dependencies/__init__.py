from typing import Annotated

from fastapi import Depends, Request

from src.app.bootstrap.container import ApplicationContainer
from src.app.interfaces.llm.llm_client import LLMClient
from src.app.interfaces.pricing_gateway import PricingGateway
from src.app.services.sales_service import SalesService


def get_container(request: Request) -> ApplicationContainer:
    return request.app.state.container  # type: ignore[no-any-return]


def get_sales_service(request: Request) -> SalesService:
    """Resolved through the container rather than constructed per request, so a
    test can override this one dependency instead of assembling a whole container."""
    return get_container(request).sales_service


def get_llm_client(request: Request) -> LLMClient:
    return get_container(request).llm_client


def get_pricing_gateway(request: Request) -> PricingGateway:
    return get_container(request).pricing_gateway


ContainerDep = Annotated[ApplicationContainer, Depends(get_container)]
SalesServiceDep = Annotated[SalesService, Depends(get_sales_service)]
LLMClientDep = Annotated[LLMClient, Depends(get_llm_client)]
PricingGatewayDep = Annotated[PricingGateway, Depends(get_pricing_gateway)]
