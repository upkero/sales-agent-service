"""Adapter: the PricingGateway and LeadGateway ports, spoken over HTTP to ops-core-api.

The Adapter pattern earns its place here rather than being decoration. The ports
are shaped like data repositories, and this class is what makes an HTTP service
satisfy that shape — mapping the paginated envelope to a plain sequence,
translating the {detail, error_code} error envelope into typed exceptions, and
keeping every httpx type from escaping. The PRESENT/UPSELL stages above it cannot
tell the difference, and the in-memory fake in the tests is the second
implementation that proves it.

One class serves both ports because it is one upstream: one client, one key, one
retry policy. The stages see two narrow interfaces, never the whole class.
"""

from collections.abc import Sequence
from decimal import Decimal
from logging import getLogger
from typing import Any

import httpx
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

from src.app.contracts.lead import Lead
from src.app.contracts.pricing import PriceQuote
from src.app.core.request_id import get_request_id
from src.app.core.resilience import retry_async
from src.app.core.settings.ops_core import OpsCoreSettings
from src.app.core.url_redaction import strip_query_from_span
from src.app.exceptions.lead import LeadNotSavedError
from src.app.exceptions.pricing import (
    PricingError,
    PricingRateLimitedError,
    PricingRejectedError,
    PricingUnavailableError,
    ServiceNotFoundError,
)
from src.app.interfaces.lead_gateway import LeadGateway
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


# ops-core-api's notes field holds 2 000 characters; the order line is far shorter, and the
# contact is cut to 120 characters by the stage that reads it.
_UNNAMED_LEAD = "Unnamed lead"
# ops-core-api's limits on a customer (CustomerCreateRequest). Name and contact are typed by
# the customer, so a long one is cut here rather than turned into a 422 and a lost lead.
_NAME_MAX = 200
_NOTES_MAX = 2_000


def _lead_notes(lead: Lead) -> str:
    """The note the front desk reads: what was asked for, what it costs, how to reach them."""
    order = f"{lead.quantity if lead.quantity is not None else '?'} x {lead.service or 'service not named'}"
    total = str(lead.total) if lead.total is not None else "not quoted"
    return (
        f"Sales chat lead. Order: {order}. Quoted total: {total}. "
        f"Contact: {lead.contact or 'not given'}. Conversation: {lead.conversation_id}."
    )


def _error_envelope(response: httpx.Response) -> tuple[str, str]:
    """(error_code, detail) from ops-core-api's {detail, error_code} envelope.

    Empty code and the start of the raw body when the response is not that envelope.
    """
    try:
        body = response.json()
        return str(body.get("error_code", "")), str(body.get("detail", ""))
    except (ValueError, AttributeError):
        return "", response.text[:200]


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


class OpsCoreGateway(PricingGateway, LeadGateway):
    def __init__(self, settings: OpsCoreSettings, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._client = client
        # Bind the retry policy once, from settings, rather than decorating the
        # method at import time with a hard-coded attempt count. One binding per
        # target, so the retry counter says which call is flaky.
        self._retrying_pricing = retry_async(
            attempts=settings.max_attempts, retry_on=_TransientError, target="ops_core.pricing"
        )(self._send_once)
        self._retrying_customers = retry_async(
            attempts=settings.max_attempts, retry_on=_TransientError, target="ops_core.customers"
        )(self._send_once)

    async def quote(self, service: str, quantity: int) -> PriceQuote:
        payload = await self._send(
            "GET",
            "/api/v1/pricing",
            params={"service": service, "quantity": quantity},
        )
        return self._to_quote(payload)

    async def list_services(self) -> Sequence[str]:
        payload = await self._send(
            "GET",
            "/api/v1/pricing/services",
            params={"limit": _CATALOGUE_PAGE_SIZE},
        )
        return [str(item["service_name"]) for item in payload.get("items", [])]

    async def save_lead(self, lead: Lead) -> None:
        """Record the lead as a `lead` customer, its order and contact in the notes.

        ops-core-api's customer has a name, a status and free-text notes and no
        contact field, so what the front desk needs to call back goes in the notes.
        Retried like a price lookup. A retry after a response that was lost can leave
        two records of one lead: a duplicate to merge costs less than a lost call-back.
        """
        name = (lead.name or "").strip()[:_NAME_MAX] or _UNNAMED_LEAD
        body = {"name": name, "status": "lead", "notes": _lead_notes(lead)[:_NOTES_MAX]}
        try:
            response = await self._retrying_customers("POST", "/api/v1/customers", json=body)
        except _TransientError as exc:
            # The exception text names the method, path and status; the body, which holds
            # the customer's details, never reaches a log.
            self._log_unreachable("customer create", exc)
            raise LeadNotSavedError() from exc
        if response.status_code >= 400:
            # A 4xx means the two services disagree about the contract: ours to fix, so loud.
            logger.error(
                "ops-core-api refused the customer record: %s",
                response.status_code,
                extra={"status_code": response.status_code, "error_code": _error_envelope(response)[0]},
            )
            raise LeadNotSavedError()

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
            response = await self._retrying_pricing(method, path, params=params)
        except _TransientError as exc:
            self._log_unreachable("pricing", exc)
            if exc.response is not None and exc.response.status_code == httpx.codes.TOO_MANY_REQUESTS:
                # Still throttled after every attempt. Pass the upstream's own
                # Retry-After along instead of reporting it as broken: "wait n
                # seconds" is the one actionable thing the caller can be told.
                retry_after = exc.response.headers.get("Retry-After", _DEFAULT_RETRY_AFTER)
                raise PricingRateLimitedError(headers={"Retry-After": retry_after}) from exc
            raise PricingUnavailableError() from exc
        if response.status_code >= 400:
            raise self._to_exception(response)
        result: dict[str, Any] = response.json()
        return result

    def _log_unreachable(self, what: str, exc: _TransientError) -> None:
        logger.warning(
            "ops-core-api %s unreachable after %d attempts: %s",
            what,
            self._settings.max_attempts,
            exc,
            extra={"attempts": self._settings.max_attempts},
        )

    async def _send_once(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """One attempt. Raises _TransientError for what is worth repeating and returns
        every other response, 4xx included, for the caller to read in its own terms."""
        try:
            response = await self._client.request(method, path, params=params, json=json)
        except httpx.TimeoutException as exc:
            raise _TransientError(f"{method} {path} timed out") from exc
        except httpx.TransportError as exc:
            raise _TransientError(f"{method} {path} failed to connect: {exc}") from exc

        if response.status_code >= 500 or response.status_code == httpx.codes.TOO_MANY_REQUESTS:
            # Server-side and possibly momentary (503 while it restarts, say). 429
            # joins them as the one 4xx that says "not now" rather than "no"; the
            # response rides along so the retry policy can honour its Retry-After.
            # Any other 4xx is a rejection, and repeating it just makes the
            # prospect wait for the same answer.
            raise _TransientError(f"{method} {path} returned {response.status_code}", response)
        return response

    def _to_exception(self, response: httpx.Response) -> PricingError:
        error_code, detail = _error_envelope(response)
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
        return PricingRejectedError()

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


async def _inject_request_id(request: httpx.Request) -> None:
    """Carry the caller's request id on to ops-core-api.

    An event hook rather than a static header: the client is built once when the
    container starts, and the id is different on every request. Reading the
    ContextVar at send time is what lets one id tie a prospect's turn to the
    pricing call it caused, in two services' logs.
    """
    if request_id := get_request_id():
        request.headers["X-Request-ID"] = request_id


def create_ops_core_gateway(
    settings: OpsCoreSettings, *, transport: httpx.AsyncBaseTransport | None = None
) -> OpsCoreGateway:
    """Factory: the one place the HTTP client for ops-core-api is built.

    `transport` is for tests: it replaces the network, and the instrumentation still wraps it.
    """
    client = httpx.AsyncClient(
        base_url=settings.base_url,
        timeout=settings.timeout_seconds,
        headers={"X-API-Key": settings.api_key.get_secret_value()},
        event_hooks={"request": [_inject_request_id]},
        transport=transport,
    )
    # A span per attempt, and the `traceparent` that makes ops-core-api's server
    # span its child. The X-Request-ID hook stays: that is the id logs are joined on.
    # The span keeps the path but not the query: `?service=` is text from the conversation.
    HTTPXClientInstrumentor().instrument_client(client, request_hook=strip_query_from_span)
    return OpsCoreGateway(settings, client)
