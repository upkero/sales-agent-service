"""The lines a customer reads word for word follow the copy rules."""

import pytest

from src.app.messages import _MESSAGES

_LONG_DASHES = ("—", "–")


@pytest.mark.parametrize(("language", "key"), [(lang, key) for lang, lines in _MESSAGES.items() for key in lines])
def test_a_customer_facing_line_has_no_long_dash(language: str, key: str) -> None:
    assert not any(dash in _MESSAGES[language][key] for dash in _LONG_DASHES)
