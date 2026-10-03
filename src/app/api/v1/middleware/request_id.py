import re
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

from src.app.core.request_id import generate_request_id, set_request_id

_HEADER = "X-Request-ID"
_SKIP_PATHS = frozenset({"/metrics", "/health/live", "/health/ready"})
# The id is echoed back, forwarded upstream and written into every log line, so
# only a plain token is taken from the caller; anything else gets a fresh one.
_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")


def register_request_id_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_id_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.url.path in _SKIP_PATHS:
            return await call_next(request)
        incoming = request.headers.get(_HEADER, "")
        request_id = incoming if _VALID_REQUEST_ID.fullmatch(incoming) else generate_request_id()
        set_request_id(request_id)
        response = await call_next(request)
        response.headers[_HEADER] = request_id
        return response
