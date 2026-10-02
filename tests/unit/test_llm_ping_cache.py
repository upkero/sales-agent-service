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


def _client(sdk: MagicMock) -> OpenAICompatibleLLMClient:
    return OpenAICompatibleLLMClient(settings=MagicMock(), client=sdk)


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
