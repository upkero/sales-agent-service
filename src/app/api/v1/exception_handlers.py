from collections.abc import Mapping
from logging import getLogger
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

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
        headers=headers,
    )


async def handle_app_exception(request: Request, exc: BaseAppException) -> JSONResponse:
    # 5xx are our faults and are logged with a stacktrace; 4xx are the caller's
    # and stay quiet. Either way the caller gets the same typed envelope, never
    # a raw traceback.
    if exc.status_code >= 500:
        logger.error(
            "Application error on %s %s: %s",
            request.method,
            request.url.path,
            exc.detail,
            exc_info=exc,
            extra=exc.extra,
        )
    return _error_response(exc.status_code, exc.detail, exc.error_code)


async def handle_request_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    logger.warning("Request validation error on %s %s", request.method, request.url.path)
    errors = exc.errors()
    for err in errors:
        # Pydantic sometimes tucks a raw exception in ctx; JSONResponse cannot
        # serialise it, so stringify before it reaches the encoder.
        if (ctx := err.get("ctx")) and isinstance(ctx.get("error"), Exception):
            ctx["error"] = str(ctx["error"])
    return _error_response(422, errors, "request_validation_error")


async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return _error_response(exc.status_code, exc.detail, "http_error", headers=exc.headers)


async def handle_unexpected_exception(request: Request, exc: Exception) -> JSONResponse:
    logger.error("Unhandled exception on %s %s", request.method, request.url.path, exc_info=exc)
    return _error_response(500, "Internal server error", "internal_server_error")


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(BaseAppException, handle_app_exception)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, handle_request_validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, handle_unexpected_exception)
