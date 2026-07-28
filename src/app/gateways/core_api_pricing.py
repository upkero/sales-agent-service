"""Adapter: the PricingGateway port, spoken over HTTP to ops-core-api.

The Adapter pattern earns its place here rather than being decoration. The port
is shaped like a data repository, and this class is what makes an HTTP service
satisfy that shape — mapping the paginated envelope to a plain sequence,
translating the {detail, error_code} error envelope into typed exceptions, and
keeping every httpx type from escaping. The PRESENT/UPSELL stages above it cannot
tell the difference, and the in-memory fake in the tests is the second
implementation that proves it.
"""

from collections.abc import Sequence
from decimal import Decimal
from logging import getLogger
from typing import Any

import httpx

from src.app.contracts.pricing import PriceQuote, PricingItem
from src.app.core.request_id import get_request_id
from src.app.core.resilience import retry_async
from src.app.core.settings.core_api import CoreApiSettings
from src.app.exceptions.pricing import (
    PricingError,
    PricingRateLimitedError,
    PricingUnavailableError,
    ServiceNotFoundError,
)
from src.app.interfaces.pricing_gateway import PricingGateway

logger = getLogger(__name__)

# The catalogue is a handful of services, so one page always covers it. Asking
# for more than exists costs nothing; paging through it would add round trips.
_CATALOGUE_PAGE_SIZE = 100

# error_code -> our exception. Switching on the code rather than the status is
# what lets the agent tell "no such service" (recoverable, offer the catalogue)
# apart from a generic 4xx.
_ERROR_CODES: dict[str, type[PricingError]] = {
    "entity_not_found": ServiceNotFoundError,
}


# What we tell our own caller to wait when ops-core-api throttled us without
# saying for how long. Short: the window it enforces is per minute.
_DEFAULT_RETRY_AFTER = "5"


class _TransientError(Exception):
    """Internal marker for failures worth repeating.

    Private to this module: it exists only to tell the retry decorator what to
    retry and never reaches a caller — the last one becomes PricingUnavailableError
    or, for a 429, PricingRateLimitedError.

    It carries the response when there was one, because the retry policy reads
    `Retry-After` off it. That read is duck-typed in core/resilience.py, which is
    how a retry policy stays reusable for things that are not HTTP.
    """

    def __init__(self, message: str, response: httpx.Response | None = None) -> None:
        super().__init__(message)
        self.response = response


class CoreApiPricingGateway(PricingGateway):
    def __init__(self, settings: CoreApiSettings, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._client = client
        # Bind the retry policy once, from settings, rather than decorating the
        # method at import time with a hard-coded attempt count.
        self._retrying_send = retry_async(attempts=settings.max_attempts, retry_on=_TransientError)(self._send_once)

    async def quote(self, service: str, quantity: int) -> PriceQuote:
        payload = await self._send(
            "GET",
            "/api/v1/pricing",
            params={"service": service, "quantity": quantity},
        )
        return self._to_quote(payload)

    async def list_services(self) -> Sequence[PricingItem]:
        payload = await self._send(
            "GET",
            "/api/v1/pricing/services",
            params={"limit": _CATALOGUE_PAGE_SIZE},
        )
        return [self._to_item(item) for item in payload.get("items", [])]

    async def ping(self) -> bool:
        # ops-core-api's readiness, not its liveness: a core that is up but cannot
        # reach its database cannot price anything, and it says so with a 503.
        # Anything other than 200 therefore means "do not send prospects here".
        # Deliberately no retry policy: a readiness probe must report the state
        # *now*, not after backing off, and it must never raise — an unreachable
        # dependency is a False, not a 500.
        try:
            response = await self._client.get("/health/ready")
        except httpx.HTTPError:
            return False
        return response.status_code == httpx.codes.OK

    async def close(self) -> None:
        await self._client.aclose()

    async def _send(self, method: str, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send with retries, and let nothing transport-shaped escape.

        The conversion happens here, outside the retry loop: _TransientError is
        the retry decorator's vocabulary and PricingUnavailableError is the
        domain's. A caller that had to know about both would be coupled to the
        retry mechanism it is meant to be insulated from.
        """
        try:
            return await self._retrying_send(method, path, params=params)
        except _TransientError as exc:
            logger.warning(
                "ops-core-api pricing unreachable after %d attempts: %s",
                self._settings.max_attempts,
                exc,
                extra={"attempts": self._settings.max_attempts},
            )
            if exc.response is not None and exc.response.status_code == httpx.codes.TOO_MANY_REQUESTS:
                # Still throttled after every attempt. Pass the upstream's own
                # Retry-After along instead of reporting it as broken: "wait n
                # seconds" is the one actionable thing the caller can be told.
                retry_after = exc.response.headers.get("Retry-After", _DEFAULT_RETRY_AFTER)
                raise PricingRateLimitedError(headers={"Retry-After": retry_after}) from exc
            raise PricingUnavailableError() from exc

    async def _send_once(self, method: str, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = await self._client.request(method, path, params=params)
        except httpx.TimeoutException as exc:
            raise _TransientError(f"{method} {path} timed out") from exc
        except httpx.TransportError as exc:
            raise _TransientError(f"{method} {path} failed to connect: {exc}") from exc

        if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
            # The one 4xx worth repeating: it says "not now", not "no". The
            # response rides along so the retry policy can honour Retry-After
            # instead of guessing with its own backoff curve.
            raise _TransientError(f"{method} {path} was rate limited", response)
        if response.status_code >= 500:
            # Server-side and possibly momentary (503 while it restarts, say). A
            # 4xx is not: repeating a rejected request just makes the prospect
            # wait for the same answer.
            raise _TransientError(f"{method} {path} returned {response.status_code}", response)
        if response.status_code >= 400:
            raise self._to_exception(response)

        result: dict[str, Any] = response.json()
        return result

    def _to_exception(self, response: httpx.Response) -> PricingError:
        try:
            body = response.json()
            error_code = str(body.get("error_code", ""))
            detail = str(body.get("detail", ""))
        except ValueError:
            error_code, detail = "", response.text[:200]

        exception_type = _ERROR_CODES.get(error_code)
        if exception_type is not None:
            return exception_type(detail or None)

        # An unmapped 4xx means this service and ops-core-api disagree about the
        # contract. That is a bug on our side, so it is logged loudly rather than
        # folded into "pricing is unavailable".
        logger.error(
            "Unmapped error from ops-core-api pricing: %s %s",
            response.status_code,
            error_code or "<no error_code>",
            extra={"status_code": response.status_code, "error_code": error_code, "detail": detail},
        )
        return PricingError(detail or "The pricing service rejected the request.")

    @staticmethod
    def _to_quote(item: dict[str, Any]) -> PriceQuote:
        # Money arrives as JSON strings ("240.00") on purpose; Decimal(str) keeps
        # the cents exact where Decimal(float) would not.
        return PriceQuote(
            service_name=str(item["service_name"]),
            unit_price=Decimal(str(item["unit_price"])),
            quantity=int(item["quantity"]),
            subtotal=Decimal(str(item["subtotal"])),
            discount_percent=Decimal(str(item["discount_percent"])),
            discount_amount=Decimal(str(item["discount_amount"])),
            total=Decimal(str(item["total"])),
        )

    @staticmethod
    def _to_item(item: dict[str, Any]) -> PricingItem:
        return PricingItem(
            service_name=str(item["service_name"]),
            unit_price=Decimal(str(item["unit_price"])),
            description=item.get("description"),
        )


async def _inject_request_id(request: httpx.Request) -> None:
    """Carry the caller's request id on to ops-core-api.

    An event hook rather than a static header: the client is built once when the
    container starts, and the id is different on every request. Reading the
    ContextVar at send time is what lets one id tie a prospect's turn to the
    pricing call it caused, in two services' logs.
    """
    if request_id := get_request_id():
        request.headers["X-Request-ID"] = request_id


def create_pricing_gateway(settings: CoreApiSettings) -> CoreApiPricingGateway:
    """Factory: the one place the HTTP client for ops-core-api is built."""
    client = httpx.AsyncClient(
        base_url=settings.base_url,
        timeout=settings.timeout_seconds,
        headers={"X-API-Key": settings.api_key.get_secret_value()},
        event_hooks={"request": [_inject_request_id]},
    )
    return CoreApiPricingGateway(settings, client)
