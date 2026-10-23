"""The server span of a request: its name, its route, its parents.

These run the production app, so they also pin what the pinned instrumentation
release gets wrong on this FastAPI (see `_use_route_templates` in `core/telemetry.py`).
This service has no route with an id in its path, so the template case is the one
literal route; the id case is covered where the routes have ids (ops-core-api).
"""

import os
from collections.abc import AsyncIterator, Iterator

import pytest
from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk.trace import ReadableSpan

from src.main import create_app
from tests import telemetry
from tests.fakes import FakePricingGateway, StubLLM, build_container, control

_AUTH = {"X-API-Key": os.environ["SECURITY_API_KEY"]}


@pytest.fixture(autouse=True)
def _spans() -> Iterator[None]:
    telemetry.clear_spans()
    yield
    telemetry.clear_spans()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.state.container = build_container(StubLLM(control("hi")), FakePricingGateway())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", headers=_AUTH) as http:
        yield http


def server_spans() -> list[ReadableSpan]:
    return [span for span in telemetry.finished_spans() if span.kind.name == "SERVER"]


async def test_the_server_span_is_named_by_its_route(client: AsyncClient) -> None:
    response = await client.post("/api/v1/turn", json={"message": "hi"})

    assert response.status_code == 200
    (span,) = server_spans()
    assert span.name == "POST /api/v1/turn"
    assert (span.attributes or {})["http.route"] == "/api/v1/turn"


async def test_the_request_duration_metric_is_labelled_by_route(client: AsyncClient) -> None:
    before = telemetry.reading("http.server.request.duration", **{"http.route": "/api/v1/turn"})

    await client.post("/api/v1/turn", json={"message": "hi"})

    assert telemetry.reading("http.server.request.duration", **{"http.route": "/api/v1/turn"}) == before + 1


async def test_a_method_the_route_does_not_allow_is_a_405_not_a_crash(client: AsyncClient) -> None:
    response = await client.put("/api/v1/turn")

    assert response.status_code == 405
    (span,) = server_spans()
    assert (span.attributes or {})["http.route"] == "/api/v1/turn"


async def test_a_cors_preflight_is_not_a_crash(client: AsyncClient) -> None:
    response = await client.options(
        "/api/v1/turn", headers={"Origin": "http://site.test", "Access-Control-Request-Method": "POST"}
    )

    assert response.status_code < 500
    assert server_spans()


async def test_a_path_that_is_no_route_gets_no_route_label(client: AsyncClient) -> None:
    response = await client.get("/api/v1/nothing-here")

    assert response.status_code == 404
    (span,) = server_spans()
    assert "http.route" not in (span.attributes or {})


async def test_the_server_span_carries_the_request_id_the_logs_show(client: AsyncClient) -> None:
    response = await client.post("/api/v1/turn", json={"message": "hi"}, headers={"X-Request-ID": "req-abc123"})

    assert response.headers["X-Request-ID"] == "req-abc123"
    (span,) = server_spans()
    assert (span.attributes or {})["request_id"] == "req-abc123"


async def test_an_inbound_traceparent_makes_the_callers_span_the_parent(client: AsyncClient) -> None:
    trace_id, caller_span = "0af7651916cd43dd8448eb211c80319c", "b7ad6b7169203331"

    await client.post(
        "/api/v1/turn", json={"message": "hi"}, headers={"traceparent": f"00-{trace_id}-{caller_span}-01"}
    )

    (span,) = server_spans()
    assert format(span.context.trace_id, "032x") == trace_id
    assert span.parent is not None
    assert format(span.parent.span_id, "016x") == caller_span


async def test_a_throttled_request_is_still_a_span(client: AsyncClient) -> None:
    # TURN_RATE_LIMIT_PER_MINUTE is 5 in the test environment.
    for _ in range(5):
        assert (await client.post("/api/v1/turn", json={"message": "hi"})).status_code == 200

    response = await client.post("/api/v1/turn", json={"message": "hi"})

    assert response.status_code == 429
    assert (server_spans()[-1].attributes or {})["http.response.status_code"] == 429
