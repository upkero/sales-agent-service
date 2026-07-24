from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

from src.app.core.request_id import generate_request_id, set_request_id

_HEADER = "X-Request-ID"
_SKIP_PATHS = frozenset({"/metrics", "/health/live", "/health/ready"})


def register_request_id_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_id_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.url.path in _SKIP_PATHS:
            return await call_next(request)
        request_id = request.headers.get(_HEADER) or generate_request_id()
        set_request_id(request_id)
        response = await call_next(request)
        response.headers[_HEADER] = request_id
        return response
