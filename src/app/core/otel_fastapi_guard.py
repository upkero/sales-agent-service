"""Keeps a wrong-method request a 405 under the pinned FastAPI instrumentation.

opentelemetry-instrumentation-fastapi 0.60b1 names a server span by walking
`app.routes` and, on a route that matches the path but not the method, reads
`route.path`. FastAPI 0.139 puts an `_IncludedRouter` in `app.routes` for every
`include_router`, and it has no `.path`. The AttributeError is raised in the
instrumentation's middleware, outside every exception handler, so `GET /api/v1/turn`
answered 500 instead of 405 (and logged a stacktrace per probe).

The guard answers "no route known" for that one case; the span is then named by its
method alone. NOTE: delete this when the pinned instrumentation handles
`_IncludedRouter` (the pin is held at 0.60b1 by livekit-agents, see core/telemetry.py).
"""

from typing import Any

from opentelemetry.instrumentation import fastapi as otel_fastapi


def guard_route_lookup() -> None:
    """Idempotent: the app factory runs more than once under tests."""
    original = otel_fastapi._get_route_details
    if getattr(original, "guarded", False):
        return

    def guarded(scope: dict[str, Any]) -> str | None:
        try:
            route: str | None = original(scope)  # type: ignore[no-untyped-call]
        except AttributeError:
            return None
        return route

    guarded.guarded = True  # type: ignore[attr-defined]
    otel_fastapi._get_route_details = guarded
