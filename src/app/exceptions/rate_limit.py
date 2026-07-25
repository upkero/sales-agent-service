from src.app.exceptions.base import BaseAppException


class RateLimitExceededError(BaseAppException):
    """Raised when a client exceeds the configured request rate.

    The turn endpoint drives a paid LLM call per request, so the cap is a cost
    control as much as an abuse control.
    """

    status_code = 429
    error_code = "rate_limit_exceeded"
    default_detail = "Too many requests. Please slow down and retry shortly."
