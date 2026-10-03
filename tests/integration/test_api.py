"""The HTTP surface, through the real app.

Nothing about the wiring is stubbed — real middleware, routing and error handling.
Only the two external dependencies (the LLM and ops-core-api) are fakes, injected
through the container, so these tests exercise the plumbing the service unit tests
cannot see.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from src.app.bootstrap.container import ApplicationContainer
from src.app.core.settings.app import get_app_settings
from src.app.interfaces.pricing_gateway import PricingGateway
from src.main import create_app
from tests.fakes import FakePricingGateway, StubLLM, UnavailablePricingGateway, build_container, control

TURN = "/api/v1/turn"


@asynccontextmanager
async def _client(script: str | list[str], pricing: PricingGateway | None = None) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.state.container = build_container(StubLLM(script), pricing or FakePricingGateway())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        yield client


async def test_liveness_is_open_and_cheap() -> None:
    async with _client(control("hi")) as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness_is_ok_when_dependencies_are_up() -> None:
    async with _client(control("hi")) as client:  # fake LLM and pricing both ping True
        response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness_fails_when_ops_core_is_unreachable() -> None:
    # The price source is a readiness dependency: down means not ready.
    async with _client(control("hi"), pricing=UnavailablePricingGateway()) as client:
        response = await client.get("/health/ready")

    assert response.status_code == 503
    assert "ops-core-api" in response.json()["detail"]


async def test_a_first_turn_returns_the_envelope_and_advances_the_stage() -> None:
    async with _client([control("Hi, I'm Alex — what can I help with?")]) as client:
        response = await client.post(TURN, json={"message": "hello"})

    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"]
    assert body["stage"] == "qualify"  # greeting handed off to qualification
    assert body["done"] is False
    assert body["handoff"] is False
    assert "Alex" in body["reply"]


async def test_a_conversation_is_continued_by_its_id() -> None:
    script = [
        control("Hello! What are you after?"),
        control("Three deep tissue massages.", service="Deep Tissue Massage", quantity=3),
        control("That's 360 in total.", objection=False),  # the same turn: PRESENT answers right away
    ]
    async with _client(script) as client:

        async def turn(message: str, conversation_id: str | None = None) -> dict[str, Any]:
            payload = {"message": message, "conversation_id": conversation_id}
            body: dict[str, Any] = (await client.post(TURN, json=payload)).json()
            return body

        first = await turn("hi")
        cid = first["conversation_id"]
        second = await turn("three deep tissue massages", cid)

    assert first["stage"] == "qualify"
    assert second["conversation_id"] == cid
    assert second["stage"] == "present"
    assert "360" in second["reply"]


async def test_the_reply_language_can_be_requested() -> None:
    async with _client(control("Здравствуйте!")) as client:
        ok = await client.post(TURN, json={"message": "hello", "language": "ru"})
        unsupported = await client.post(TURN, json={"message": "hello", "language": "de"})

    assert ok.status_code == 200
    assert unsupported.status_code == 422


async def test_an_empty_message_is_a_typed_validation_error() -> None:
    async with _client(control("hi")) as client:
        response = await client.post(TURN, json={"message": ""})

    assert response.status_code == 422
    assert response.json()["error_code"] == "request_validation_error"


async def test_a_blank_message_is_rejected_before_the_model_is_called() -> None:
    llm = StubLLM([])  # any call would exhaust the script and fail the request
    app = create_app()
    app.state.container = build_container(llm, FakePricingGateway())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(TURN, json={"message": " \n\t "})

    assert response.status_code == 422
    assert response.json()["error_code"] == "request_validation_error"
    assert llm.calls == []


async def test_the_request_id_is_echoed() -> None:
    async with _client([control("hi")]) as client:
        response = await client.post(TURN, json={"message": "hello"}, headers={"X-Request-ID": "abc-123"})

    assert response.headers["X-Request-ID"] == "abc-123"


async def test_the_turn_endpoint_is_rate_limited() -> None:
    """It drives a paid LLM call per request, so it stays bounded (5/min in tests)."""
    async with _client(control("hi")) as client:  # str script: same reply every call
        statuses = [(await client.post(TURN, json={"message": "hi"})).status_code for _ in range(6)]

    assert statuses.count(200) == 5
    assert statuses[-1] == 429


async def test_a_rate_limited_response_says_when_to_retry() -> None:
    async with _client(control("hi")) as client:
        for _ in range(6):
            response = await client.post(TURN, json={"message": "hi"})

    assert response.status_code == 429
    assert response.json()["error_code"] == "rate_limit_exceeded"
    assert "Retry-After" in response.headers


async def test_health_is_never_rate_limited() -> None:
    """The container runtime polls it; throttling it would restart a healthy service."""
    async with _client(control("hi")) as client:
        statuses = [(await client.get("/health/live")).status_code for _ in range(20)]

    assert set(statuses) == {200}


async def test_the_turn_endpoint_is_open_when_no_key_is_configured() -> None:
    # The default in the test env: no INBOUND_API_KEY, so no header is needed.
    async with _client([control("hi")]) as client:
        response = await client.post(TURN, json={"message": "hello"})

    assert response.status_code == 200


async def test_inbound_auth_is_enforced_when_a_key_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INBOUND_API_KEY", "s3cret-inbound-key-value")
    get_app_settings.cache_clear()  # settings are cached; rebuild them with the key set
    try:
        async with _client(control("hi")) as client:
            missing = await client.post(TURN, json={"message": "hi"})
            wrong = await client.post(TURN, json={"message": "hi"}, headers={"X-API-Key": "nope"})
            right = await client.post(TURN, json={"message": "hi"}, headers={"X-API-Key": "s3cret-inbound-key-value"})

        assert missing.status_code == 401
        assert missing.json()["error_code"] == "invalid_api_key"
        assert wrong.status_code == 401
        assert right.status_code == 200
    finally:
        get_app_settings.cache_clear()  # leave settings clean for other tests


async def test_a_form_body_is_a_validation_error_not_a_crash() -> None:
    # curl's default content type when -H 'Content-Type: application/json' is
    # forgotten. Pydantic keeps the raw bytes in the error, undecodable ones too.
    async with _client(control("hi")) as client:
        for body in (b"message=hi", b"\xff\xfe"):
            response = await client.post(
                TURN,
                content=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

            assert response.status_code == 422
            assert response.json()["error_code"] == "request_validation_error"


async def test_an_unhandled_error_is_a_500_that_still_carries_the_request_id() -> None:
    async def boom() -> None:
        raise RuntimeError("boom")

    app = create_app()
    app.add_api_route("/boom", boom)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/boom", headers={"X-Request-ID": "abc-123"})

    assert response.status_code == 500
    assert response.json()["error_code"] == "internal_server_error"
    assert response.headers["X-Request-ID"] == "abc-123"


async def test_a_malformed_request_id_is_replaced() -> None:
    async with _client(control("hi")) as client:
        # Echoed, forwarded upstream and logged, so a markup or oversized id is replaced.
        for bad in ("attacker-<script>", "r" * 129):
            response = await client.get("/no-such-route", headers={"X-Request-ID": bad})

            assert UUID(response.headers["X-Request-ID"])


async def test_a_misconfiguration_fails_the_boot_not_the_first_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    # A container property that cannot be built stands in for any misconfiguration.
    def broken(self: ApplicationContainer) -> None:
        raise RuntimeError("misconfigured")

    monkeypatch.setattr(ApplicationContainer, "sales_service", property(broken))
    app = create_app()

    with pytest.raises(RuntimeError, match="misconfigured"):
        async with app.router.lifespan_context(app):
            pass
