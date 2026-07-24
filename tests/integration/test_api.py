"""The HTTP surface, through the real app.

Nothing about the wiring is stubbed — real middleware, routing and error handling.
Only the two external dependencies (the LLM and ops-core-api) are fakes, injected
through the container, so these tests exercise the plumbing the service unit tests
cannot see.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from httpx import ASGITransport, AsyncClient

from src.main import create_app
from tests.fakes import FakePricingGateway, StubLLM, build_container, control

TURN = "/api/v1/sales-agent/turn"


@asynccontextmanager
async def _client(script: str | list[str]) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.state.container = build_container(StubLLM(script), FakePricingGateway())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        yield client


async def test_liveness_is_open_and_cheap() -> None:
    async with _client(control("hi")) as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


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
        control("That's 360 in total.", objection=False),
    ]
    async with _client(script) as client:

        async def turn(message: str, conversation_id: str | None = None) -> dict:
            payload = {"message": message, "conversation_id": conversation_id}
            return (await client.post(TURN, json=payload)).json()

        first = await turn("hi")
        cid = first["conversation_id"]
        second = await turn("three deep tissue massages", cid)
        third = await turn("how much?", cid)

    assert second["stage"] == "present"
    assert third["stage"] == "present"
    assert "360" in third["reply"]


async def test_an_empty_message_is_a_typed_validation_error() -> None:
    async with _client(control("hi")) as client:
        response = await client.post(TURN, json={"message": ""})

    assert response.status_code == 422
    assert response.json()["error_code"] == "request_validation_error"


async def test_the_request_id_is_echoed() -> None:
    async with _client([control("hi")]) as client:
        response = await client.post(TURN, json={"message": "hello"}, headers={"X-Request-ID": "abc-123"})

    assert response.headers["X-Request-ID"] == "abc-123"
