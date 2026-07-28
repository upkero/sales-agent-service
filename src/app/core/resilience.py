"""Retry policy, defined once.

Every outbound call that can fail transiently uses this decorator rather than
its own loop, so the backoff curve, the jitter and the log line are identical
wherever they appear. The OpenAI-compatible audio clients are the deliberate
exception: the SDK already retries internally, and stacking two policies on one
call turns a 10-second timeout into a minute of silence on a live phone call.
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


def _retry_after_seconds(exception: BaseException) -> float | None:
    """How long an upstream asked us to wait, if it said so at all.

    Duck-typed on purpose: it reads `exception.response.headers`, which every
    httpx status error carries, without this module importing httpx. A retry
    policy that knows about HTTP is a retry policy that cannot be reused for
    anything that is not HTTP.
    """
    response = getattr(exception, "response", None)
    if response is None:
        return None
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        # The HTTP-date form is legal but rare here; fall back to the backoff curve
        # rather than guessing at a date format.
        return None


def _wait_honouring_retry_after(max_wait_seconds: float) -> Callable[[RetryCallState], float]:
    """Exponential backoff with jitter, overridden by an upstream `Retry-After`.

    A server that says "come back in 30 seconds" knows more about its own recovery
    than our curve does. Ignoring the header is how a client turns someone else's
    429 into an outage of its own making.
    """
    backoff = wait_exponential_jitter(initial=0.1, max=max_wait_seconds)

    def _wait(state: RetryCallState) -> float:
        exception = state.outcome.exception() if state.outcome else None
        if exception is not None and (override := _retry_after_seconds(exception)) is not None:
            return override
        return backoff(state)

    return _wait


def retry_async(
    *,
    attempts: int,
    retry_on: type[Exception] | tuple[type[Exception], ...],
    max_wait_seconds: float = 2.0,
) -> Callable[[Callable[_P, Awaitable[_R]]], Callable[_P, Awaitable[_R]]]:
    """Retry an async call with exponential backoff, jitter and `Retry-After`.

    Jitter matters more than it looks: without it, every agent worker that lost
    the same core-api restart retries in lockstep and re-creates the outage it
    is backing off from.

    `retry_on` is required rather than defaulting to Exception. Retrying a 409
    "slot taken" would ask the same doomed question three times while the guest
    waits, so callers name the failures that are actually worth repeating.

    `attempts` is the total number of tries including the first, not the number
    of repeats — the same meaning `OPS_CORE_MAX_ATTEMPTS` carries in settings.
    """

    def decorator(func: Callable[_P, Awaitable[_R]]) -> Callable[_P, Awaitable[_R]]:
        async def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            retrying = AsyncRetrying(
                stop=stop_after_attempt(attempts),
                wait=_wait_honouring_retry_after(max_wait_seconds),
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
