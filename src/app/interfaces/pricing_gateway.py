from abc import ABC, abstractmethod
from collections.abc import Sequence

from src.app.contracts.pricing import PriceQuote, PricingItem


class PricingGateway(ABC):
    """The port through which the agent reaches the price list.

    Deliberately shaped like a plain data repository even though the only
    implementation talks HTTP to ops-core-api. That is the point of the Adapter
    behind it: the PRESENT and UPSELL stages cannot tell whether a quote came from
    a SQL row or a JSON response, so the transport can change without the selling
    logic noticing — and the in-memory fake in the tests is the second
    implementation that proves it.
    """

    @abstractmethod
    async def quote(self, service: str, quantity: int) -> PriceQuote:
        """Price `quantity` units of `service`, with any volume discount applied.

        Raises ServiceNotFoundError if the service is not in the catalogue and
        PricingUnavailableError if the price list cannot be reached.
        """

    @abstractmethod
    async def list_services(self) -> Sequence[PricingItem]:
        """The sellable catalogue, used to ground the agent in real offerings and
        to recover when a prospect names a service that does not exist."""

    @abstractmethod
    async def ping(self) -> bool:
        """Is the price source reachable right now? A cheap, unauthenticated,
        no-retry liveness check for the readiness probe — True if up, False if not,
        never raising."""
