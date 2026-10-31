"""The HTTP adapter, driven through a mock transport.

Only the wire is faked, so the retry policy, the status mapping and the error
envelope translation all run for real — the parts a fake gateway cannot exercise.
"""

import json
import logging
from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr

from src.app.contracts.lead import Lead
from src.app.core.request_id import set_request_id
from src.app.core.settings.ops_core import OpsCoreSettings
from src.app.exceptions.lead import LeadNotSavedError
from src.app.exceptions.pricing import (
    PricingError,
    PricingRateLimitedError,
    PricingUnavailableError,
    ServiceNotFoundError,
)
from src.app.gateways.ops_core import OpsCoreGateway, _inject_request_id
from tests.fakes import logged_text

_ATTEMPTS = 2


def _gateway(handler: httpx.MockTransport) -> OpsCoreGateway:
    settings = OpsCoreSettings(api_key=SecretStr("test-key-1234567890"), max_attempts=_ATTEMPTS)
    client = httpx.AsyncClient(transport=handler, base_url=settings.base_url)
    return OpsCoreGateway(settings, client)


async def test_a_persistent_429_becomes_a_429_with_retry_after() -> None:
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        # Retry-After 0 keeps the test instant and proves the header is honoured
        # rather than the backoff curve, which would sleep.
        return httpx.Response(429, headers={"Retry-After": "0"}, json={"detail": "slow down"})

    with pytest.raises(PricingRateLimitedError) as caught:
        await _gateway(httpx.MockTransport(respond)).quote("Deep Tissue Massage", 3)

    assert len(calls) == _ATTEMPTS  # retried, not rejected on the first 429
    assert caught.value.status_code == 429  # never 502/503: busy is not broken
    assert caught.value.headers["Retry-After"] == "0"


async def test_a_persistent_503_is_reported_as_unavailable() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(503))

    with pytest.raises(PricingUnavailableError):
        await _gateway(transport).quote("Deep Tissue Massage", 3)


async def test_a_known_error_code_becomes_its_typed_exception() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(404, json={"detail": "no such service", "error_code": "entity_not_found"})
    )

    with pytest.raises(ServiceNotFoundError):
        await _gateway(transport).quote("Hot Stone Facial", 1)


async def test_the_incoming_request_id_travels_to_ops_core_api() -> None:
    sent: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json=_QUOTE_BODY)

    settings = OpsCoreSettings(api_key=SecretStr("test-key-1234567890"))
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(respond),
        base_url=settings.base_url,
        event_hooks={"request": [_inject_request_id]},
    )
    set_request_id("turn-42")

    await OpsCoreGateway(settings, client).quote("Deep Tissue Massage", 3)

    # One id spans both services' logs, which is the whole point of accepting
    # the header at the edge and never forwarding it.
    assert sent[0].headers["X-Request-ID"] == "turn-42"


_QUOTE_BODY = {
    "service_name": "Deep Tissue Massage",
    "unit_price": "120.00",
    "quantity": 3,
    "subtotal": "360.00",
    "discount_percent": "0",
    "discount_amount": "0.00",
    "total": "360.00",
}


async def test_the_catalogue_is_the_service_names_ops_core_lists() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/pricing/services"
        return httpx.Response(200, json={"items": [{"service_name": "Deep Tissue Massage", "unit_price": "120.00"}]})

    assert await _gateway(httpx.MockTransport(respond)).list_services() == ["Deep Tissue Massage"]


async def test_an_unmapped_rejection_is_a_502_that_does_not_echo_ops_core() -> None:
    # A wrong OPS_CORE_API_KEY: ops-core's 401 detail must not reach our caller,
    # who would read "invalid API key" as a problem with their own key.
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "Missing or invalid API key.", "error_code": "invalid_api_key"})

    with pytest.raises(PricingError) as caught:
        await _gateway(httpx.MockTransport(respond)).quote("Deep Tissue Massage", 3)

    assert caught.value.status_code == 502
    assert caught.value.error_code == "pricing_rejected"
    assert "API key" not in caught.value.detail


_LEAD = Lead(
    conversation_id="conv-7",
    name="Dana Smith",
    contact="+1 555 0100",
    service="Deep Tissue Massage",
    quantity=6,
    total=Decimal("648.00"),
)


async def test_a_lead_is_posted_as_a_lead_customer_with_the_order_and_contact_in_the_notes() -> None:
    sent: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(201, json={"id": "00000000-0000-0000-0000-000000000001"})

    await _gateway(httpx.MockTransport(respond)).save_lead(_LEAD)

    (request,) = sent
    assert (request.method, request.url.path) == ("POST", "/api/v1/customers")
    body = json.loads(request.content)
    assert body["name"] == "Dana Smith"
    assert body["status"] == "lead"
    for fact in ("6 x Deep Tissue Massage", "648.00", "+1 555 0100", "conv-7"):
        assert fact in body["notes"]
    assert len(body["notes"]) <= 2_000


async def test_a_lead_without_a_name_or_contact_still_gets_a_record() -> None:
    sent: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(201, json={})

    bare = Lead(conversation_id="conv-8", name=None, contact=None, service=None, quantity=None, total=None)
    await _gateway(httpx.MockTransport(respond)).save_lead(bare)

    body = json.loads(sent[0].content)
    assert body["name"]  # ops-core-api rejects a blank one
    assert "not given" in body["notes"]


async def test_an_overlong_name_or_contact_is_cut_to_what_ops_core_accepts() -> None:
    sent: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(201, json={})

    long = Lead(conversation_id="conv-9", name="N" * 500, contact="c" * 5_000, service=None, quantity=None, total=None)
    await _gateway(httpx.MockTransport(respond)).save_lead(long)

    body = json.loads(sent[0].content)
    assert len(body["name"]) == 200
    assert len(body["notes"]) == 2_000


async def test_a_lead_save_is_retried_like_a_price_lookup() -> None:
    answers = iter([httpx.Response(503), httpx.Response(201, json={})])
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return next(answers)

    await _gateway(httpx.MockTransport(respond)).save_lead(_LEAD)

    assert len(calls) == _ATTEMPTS


async def test_a_persistent_failure_is_a_typed_error_and_the_log_has_no_personal_data(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(503))

    with caplog.at_level(logging.INFO), pytest.raises(LeadNotSavedError):
        await _gateway(transport).save_lead(_LEAD)

    logged = logged_text(caplog.records)
    assert "Dana" not in logged
    assert "555 0100" not in logged


async def test_a_rejected_lead_is_not_retried_and_is_logged_by_code(caplog: pytest.LogCaptureFixture) -> None:
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(401, json={"detail": "Missing or invalid API key.", "error_code": "invalid_api_key"})

    with caplog.at_level(logging.ERROR), pytest.raises(LeadNotSavedError):
        await _gateway(httpx.MockTransport(respond)).save_lead(_LEAD)

    assert len(calls) == 1
    assert "invalid_api_key" in str([record.__dict__ for record in caplog.records])
