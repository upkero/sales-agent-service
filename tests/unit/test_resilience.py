"""How the retry policy treats an upstream Retry-After."""

from collections.abc import Awaitable, Callable
from types import SimpleNamespace

import pytest

from src.app.core.resilience import MAX_HONOURED_RETRY_AFTER_SECONDS, retry_async


class _Throttled(Exception):
    """Shaped like an httpx status error: the policy only reads response.headers."""

    def __init__(self, retry_after: float) -> None:
        super().__init__("429")
        self.response = SimpleNamespace(headers={"Retry-After": str(retry_after)})


def _always_throttled(retry_after: float) -> tuple[list[int], Callable[[], Awaitable[None]]]:
    calls: list[int] = []

    @retry_async(attempts=3, retry_on=_Throttled)
    async def call() -> None:
        calls.append(1)
        raise _Throttled(retry_after)

    return calls, call


async def test_a_short_retry_after_is_honoured_and_retried() -> None:
    calls, call = _always_throttled(0)

    with pytest.raises(_Throttled):
        await call()

    assert len(calls) == 3


async def test_a_retry_after_beyond_the_cap_is_passed_on_without_sleeping() -> None:
    # Sleeping 60 s inside a client's request is worse than an honest 429 now.
    calls, call = _always_throttled(MAX_HONOURED_RETRY_AFTER_SECONDS + 55)

    with pytest.raises(_Throttled) as raised:
        await call()

    assert len(calls) == 1
    assert raised.value.response.headers["Retry-After"] == str(MAX_HONOURED_RETRY_AFTER_SECONDS + 55)
