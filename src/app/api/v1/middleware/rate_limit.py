"""A per-IP cap on the one endpoint that costs money to call.

`POST /api/v1/turn` drives a paid LLM call on every request, so an open,
unbounded endpoint is a direct route to a surprise bill. This caps it per client.
Everything else (health) is cheap and left alone.

Built on `limits` directly rather than slowapi's decorators: with a single
guarded path, the decorator machinery and its route-resolution quirks buy nothing
over counting the hit in one middleware.
"""

import time
from collections.abc import Awaitable, Callable
from logging import getLogger

from fastapi import FastAPI, Request, Response
from limits import parse
from limits.storage import MemoryStorage
from limits.strategies import FixedWindowRateLimiter

from src.app.api.v1.exception_handlers import error_response_from_exception
from src.app.core.settings.app import get_app_settings
from src.app.exceptions.rate_limit import RateLimitExceededError

logger = getLogger(__name__)

_GUARDED_PATH = "/api/v1/turn"

# NOTE: in-memory counters, so the window is per process. Correct while the
# Dockerfile pins uvicorn to one worker; swap MemoryStorage for Redis storage if
# this is ever scaled out (same reason the conversation store is a port).
_storage = MemoryStorage()
_limiter = FixedWindowRateLimiter(_storage)


def reset_rate_limit() -> None:
    """Drop all counters. Used by tests to keep cases independent."""
    _storage.reset()


def _client_key(request: Request) -> str:
    # request.client is None for ASGI transports without a peer (the test client,
    # for one), and a shared bucket is safer there than no bucket.
    return request.client.host if request.client else "unknown"


def register_rate_limiting(app: FastAPI) -> None:
    limit = parse(f"{get_app_settings().turn_rate_limit_per_minute}/minute")

    @app.middleware("http")
    async def turn_rate_limit_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.url.path.rstrip("/") != _GUARDED_PATH:
            return await call_next(request)

        identifier = _client_key(request)
        if not _limiter.hit(limit, identifier, _GUARDED_PATH):
            logger.warning("Turn rate limit exceeded for %s", identifier, extra={"client": identifier})
            # Returned rather than raised: exception handlers live inside the
            # middleware stack, so a raise here escapes them and becomes a 500.
            return error_response_from_exception(
                RateLimitExceededError(f"Rate limit exceeded: {limit}."),
                headers=_headers(limit, identifier),
            )

        response = await call_next(request)
        response.headers.update(_headers(limit, identifier))
        return response


def _headers(limit: object, identifier: str) -> dict[str, str]:
    stats = _limiter.get_window_stats(limit, identifier, _GUARDED_PATH)  # type: ignore[arg-type]
    return {
        "Retry-After": str(max(0, int(stats.reset_time - time.time()))),
        "X-RateLimit-Limit": str(getattr(limit, "amount", "")),
        "X-RateLimit-Remaining": str(stats.remaining),
        "X-RateLimit-Reset": str(int(stats.reset_time)),
    }
