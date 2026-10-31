import logging
from collections.abc import Iterator

import pytest

from src.app.core.logging import setup_logging
from src.app.core.settings.logging import LoggingSettings


@pytest.fixture(autouse=True)
def _restore_loggers() -> Iterator[None]:
    names = ("", "uvicorn", "uvicorn.error", "uvicorn.access")
    saved = {name: (logging.getLogger(name).handlers[:], logging.getLogger(name).propagate) for name in names}
    yield
    for name, (handlers, propagate) in saved.items():
        logging.getLogger(name).handlers, logging.getLogger(name).propagate = handlers, propagate


def test_a_disabled_access_log_stays_disabled() -> None:
    # What `uvicorn --no-access-log` leaves behind before the app module is imported.
    access = logging.getLogger("uvicorn.access")
    access.handlers, access.propagate = [], False

    setup_logging(LoggingSettings())

    assert access.propagate is False
    assert access.handlers == []


def test_uvicorn_and_its_error_log_go_through_the_json_handler() -> None:
    setup_logging(LoggingSettings())

    assert logging.getLogger("uvicorn").propagate is True
    assert logging.getLogger("uvicorn.error").propagate is True
