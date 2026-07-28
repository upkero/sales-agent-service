"""The HTTP adapter, driven through a mock transport.

Only the wire is faked, so the retry policy, the status mapping and the error
envelope translation all run for real — the parts a fake gateway cannot exercise.
"""

import httpx
import pytest
from pydantic import SecretStr

from src.app.core.settings.core_api import CoreApiSettings
from src.app.exceptions.pricing import PricingRateLimitedError, PricingUnavailableError, ServiceNotFoundError
from src.app.repositories.core_api_pricing import CoreApiPricingGateway

_ATTEMPTS = 2


def _gateway(handler: httpx.MockTransport) -> CoreApiPricingGateway:
    settings = CoreApiSettings(api_key=SecretStr("test-key-1234567890"), max_attempts=_ATTEMPTS)
    client = httpx.AsyncClient(transport=handler, base_url=settings.base_url)
    return CoreApiPricingGateway(settings, client)


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
