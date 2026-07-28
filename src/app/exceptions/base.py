from collections.abc import Mapping
from typing import Any


class BaseAppException(Exception):
    """Base exception for application-specific failures.

    Every app exception carries the status code, machine-readable error code and
    human detail the API's exception handler needs to render a uniform JSON
    envelope. Nothing in this service raises a bare Exception across a layer
    boundary — that is what turns a bug into an unhandled 500 stacktrace instead
    of a typed, logged error.
    """

    status_code = 500
    error_code = "app_error"
    default_detail = "Application error."

    def __init__(
        self,
        detail: str | None = None,
        *,
        status_code: int | None = None,
        error_code: str | None = None,
        extra: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.detail = detail or self.default_detail
        self.status_code = status_code or type(self).status_code
        self.error_code = error_code or type(self).error_code
        self.extra = dict(extra or {})
        # Response headers the envelope must carry. Empty for almost every
        # failure; a 429 is the case that has something to say (Retry-After),
        # and it belongs on the exception rather than in a special-cased handler.
        self.headers = dict(headers or {})
        super().__init__(self.detail)
