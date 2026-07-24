"""Retry policy, defined once.

Every outbound call that can fail transiently uses this decorator rather than
its own loop, so the backoff curve, the jitter and the log line are identical
wherever they appear (DRY). The LLM SDK is the deliberate exception: it already
retries internally, and stacking two policies on one call doubles the latency a
prospect waits through.
"""

from collections.abc import Awaitable, Callable
from logging import getLogger
from typing import ParamSpec, TypeVar

from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

logger = getLogger(__name__)

_P = ParamSpec("_P")
_R = TypeVar("_R")


def _log_attempt(state: RetryCallState) -> None:
    exception = state.outcome.exception() if state.outcome else None
    logger.warning(
        "Retrying %s after attempt %d: %s",
        state.fn.__qualname__ if state.fn else "call",
        state.attempt_number,
        exception,
        extra={"attempt": state.attempt_number, "error": str(exception)},
    )


def retry_async(
    *,
    attempts: int,
    retry_on: type[Exception] | tuple[type[Exception], ...],
    max_wait_seconds: float = 2.0,
) -> Callable[[Callable[_P, Awaitable[_R]]], Callable[_P, Awaitable[_R]]]:
    """Retry an async call with exponential backoff and jitter.

    Jitter matters more than it looks: without it, every worker that lost the
    same core-api restart retries in lockstep and re-creates the outage it is
    backing off from.

    `retry_on` is required rather than defaulting to Exception. Retrying a 404
    "no such service" would ask the same doomed question three times while the
    prospect waits, so callers name the failures that are actually worth repeating.
    """

    def decorator(func: Callable[_P, Awaitable[_R]]) -> Callable[_P, Awaitable[_R]]:
        async def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            retrying = AsyncRetrying(
                stop=stop_after_attempt(attempts),
                wait=wait_exponential_jitter(initial=0.1, max=max_wait_seconds),
                retry=retry_if_exception_type(retry_on),
                before_sleep=_log_attempt,
                reraise=True,
            )
            async for attempt in retrying:
                with attempt:
                    return await func(*args, **kwargs)
            raise AssertionError("unreachable: reraise=True guarantees an exit")

        return wrapper

    return decorator
