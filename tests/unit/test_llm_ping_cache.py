from unittest.mock import AsyncMock, MagicMock

import pytest
from openai import OpenAIError

from src.app.llm import openai_compatible_llm_client as module
from src.app.llm.openai_compatible_llm_client import OpenAICompatibleLLMClient


class _Clock:
    now = 1000.0


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    _Clock.now = 1000.0
    monkeypatch.setattr(module.time, "monotonic", lambda: _Clock.now)
    return _Clock


def _client(sdk: MagicMock, ok_ttl: float = 30.0, failed_ttl: float = 5.0) -> OpenAICompatibleLLMClient:
    settings = MagicMock(ping_ok_ttl_seconds=ok_ttl, ping_failed_ttl_seconds=failed_ttl)
    return OpenAICompatibleLLMClient(settings=settings, client=sdk)


async def test_a_healthy_ping_is_reused_for_thirty_seconds(clock: type[_Clock]) -> None:
    sdk = MagicMock()
    sdk.models.list = AsyncMock()
    client = _client(sdk)

    assert await client.ping() is True
    clock.now += 29
    assert await client.ping() is True
    assert sdk.models.list.await_count == 1

    clock.now += 2
    assert await client.ping() is True
    assert sdk.models.list.await_count == 2


async def test_a_failed_ping_is_reused_for_only_five_seconds(clock: type[_Clock]) -> None:
    sdk = MagicMock()
    sdk.models.list = AsyncMock(side_effect=OpenAIError("down"))
    client = _client(sdk)

    assert await client.ping() is False
    clock.now += 4
    assert await client.ping() is False
    assert sdk.models.list.await_count == 1

    # The provider comes back: the next poll after the short TTL sees it.
    sdk.models.list = AsyncMock()
    clock.now += 2
    assert await client.ping() is True


async def test_the_lifetimes_come_from_settings_and_zero_disables_the_cache(clock: type[_Clock]) -> None:
    sdk = MagicMock()
    sdk.models.list = AsyncMock()
    client = _client(sdk, ok_ttl=0.0)

    await client.ping()
    await client.ping()

    assert sdk.models.list.await_count == 2
