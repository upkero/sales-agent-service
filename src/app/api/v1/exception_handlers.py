from collections.abc import Mapping
from logging import getLogger
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.app.core.request_id import get_request_id
from src.app.exceptions.base import BaseAppException

logger = getLogger(__name__)


def _error_response(
    status: int,
    detail: Any,
    error_code: str,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"detail": detail, "error_code": error_code},
        headers=dict(headers) if headers else None,
    )


def error_response_from_exception(
    exc: BaseAppException,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Render an app exception into the uniform error envelope.

    Public because middleware cannot rely on the registered exception handlers:
    those live in Starlette's ExceptionMiddleware, which sits *inside* the HTTP
    middleware stack, so an exception raised in a middleware propagates past it
    and surfaces as a raw 500. Middleware returns this instead of raising, which
    keeps one place that knows the envelope format.

    A service whose rate limiter is a dependency rather than middleware never
    calls this: dependencies run inside the handler scope, so raising there is
    already caught below.
    """
    return _error_response(exc.status_code, exc.detail, exc.error_code, headers=headers)


async def handle_app_exception(request: Request, exc: BaseAppException) -> JSONResponse:
    # 5xx are our fault and get a stacktrace; 4xx are the caller's and stay quiet.
    # Either way the caller receives the same typed envelope, never a traceback.
    if exc.status_code >= 500:
        logger.error(
            "Application error on %s %s: %s",
            request.method,
            request.url.path,
            exc.detail,
            exc_info=exc,
            extra=exc.extra,
        )
    # `or None` rather than the mapping: an empty one would still be handed to
    # JSONResponse, and only a 429 normally has anything to say (Retry-After).
    return error_response_from_exception(exc, headers=exc.headers or None)


async def handle_request_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    logger.warning("Request validation error on %s %s", request.method, request.url.path)
    # The errors are not plain JSON: a non-JSON body (a form post, say) leaves the
    # raw bytes in `input`, and pydantic tucks exception objects into `ctx`.
    # jsonable_encoder is what FastAPI's own handler uses; the two custom encoders
    # keep undecodable bytes and exception messages from turning a 422 into a 500.
    errors = jsonable_encoder(
        exc.errors(),
        custom_encoder={bytes: lambda raw: raw.decode(errors="replace"), Exception: str},
    )
    return _error_response(422, errors, "request_validation_error")


async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return _error_response(exc.status_code, exc.detail, "http_error", headers=exc.headers)


async def handle_unexpected_exception(request: Request, exc: Exception) -> JSONResponse:
    logger.error("Unhandled exception on %s %s", request.method, request.url.path, exc_info=exc)
    # Starlette runs this handler in ServerErrorMiddleware, outside every other
    # middleware, so the request-id middleware never sees this response. The id it
    # set is still in the context (same task), so the header is added here.
    request_id = get_request_id()
    headers = {"X-Request-ID": request_id} if request_id else None
    return _error_response(500, "Internal server error", "internal_server_error", headers=headers)


def register_exception_handlers(app: FastAPI) -> None:
    # Starlette types handlers as taking a bare Exception; ours take the narrower
    # concrete type they are registered for — a known typing mismatch, not a bug.
    app.add_exception_handler(BaseAppException, handle_app_exception)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, handle_request_validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, handle_unexpected_exception)
