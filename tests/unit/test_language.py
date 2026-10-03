"""The reply language belongs to the conversation: what the caller asked for, else
the language of the prospect's first message, and AGENT_LANGUAGE only when the
first message says nothing either way."""

from collections.abc import Iterator

import pytest

from src.app.core.settings.agent import get_agent_settings
from src.app.messages import get_message
from src.app.services.sales.service import SalesService
from tests.fakes import FakePricingGateway, StubLLM, build_container, control


@pytest.fixture
def russian_by_default(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The live demo's configuration: AGENT_LANGUAGE=ru."""
    monkeypatch.setenv("AGENT_LANGUAGE", "ru")
    get_agent_settings.cache_clear()
    yield
    get_agent_settings.cache_clear()


def _service(llm: StubLLM) -> SalesService:
    return build_container(llm, FakePricingGateway()).sales_service


def _persona_language(llm: StubLLM) -> str:
    system = llm.calls[-1][0].content
    return "Russian" if "in Russian" in system else "English" if "in English" in system else "?"


@pytest.mark.usefixtures("russian_by_default")
async def test_an_english_hello_is_answered_in_english() -> None:
    llm = StubLLM(control("Hi!"))
    service = _service(llm)

    await service.take_turn(None, "hello")

    assert _persona_language(llm) == "English"


async def test_a_cyrillic_first_message_is_answered_in_russian() -> None:
    llm = StubLLM(control("Здравствуйте!"))

    await _service(llm).take_turn(None, "привет")

    assert _persona_language(llm) == "Russian"


@pytest.mark.usefixtures("russian_by_default")
async def test_the_first_message_sets_the_language_for_the_whole_conversation() -> None:
    llm = StubLLM(control("Hi!"))
    service = _service(llm)

    first = await service.take_turn(None, "hello")
    # "ok" is Latin too; a later Cyrillic word must not flip it either.
    await service.take_turn(first.conversation_id, "Алекс, ok")

    assert _persona_language(llm) == "English"


async def test_the_requested_language_wins_over_the_message() -> None:
    llm = StubLLM(control("Здравствуйте!"))
    service = _service(llm)

    first = await service.take_turn(None, "hello", language="ru")
    assert _persona_language(llm) == "Russian"

    await service.take_turn(first.conversation_id, "and another thing")
    assert _persona_language(llm) == "Russian"  # kept without being sent again


@pytest.mark.usefixtures("russian_by_default")
async def test_a_message_without_letters_falls_back_to_the_configured_language() -> None:
    llm = StubLLM(control("Здравствуйте!"))

    await _service(llm).take_turn(None, "👋 2")

    assert _persona_language(llm) == "Russian"


@pytest.mark.usefixtures("russian_by_default")
async def test_fixed_messages_follow_the_conversation_language() -> None:
    # The model never returns usable JSON, so the agent falls back to its fixed line.
    outcome = await _service(StubLLM("not json")).take_turn(None, "hello")

    assert outcome.reply == get_message("en", "clarifier")
