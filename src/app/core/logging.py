import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from src.app.core.request_id import get_request_id
from src.app.core.settings.logging import LoggingSettings

_STDLIB_ATTRS = frozenset(logging.LogRecord(
    "", 0, "", 0, "", (), None
).__dict__.keys()) | {"message", "asctime"}

_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        data: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.message,
        }
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        # Any non-stdlib LogRecord attribute is structured context passed via
        # `extra={...}`, so it is merged into the JSON payload as-is. That is how a
        # service emits its own fields without wrapping the logger.
        for key, value in record.__dict__.items():
            if key not in _STDLIB_ATTRS:
                data[key] = value
        return json.dumps(data, default=str)


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or "-"
        return True


class _TextFormatter(logging.Formatter):
    _FMT = "%(asctime)s [%(levelname)s] %(name)s [%(request_id)s]: %(message)s"

    def __init__(self) -> None:
        super().__init__(fmt=self._FMT, datefmt="%Y-%m-%dT%H:%M:%S")


def setup_logging(settings: LoggingSettings) -> None:
    formatter: logging.Formatter = (
        _JsonFormatter() if settings.format == "json" else _TextFormatter()
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(_RequestIdFilter())

    root = logging.getLogger()
    root.setLevel(settings.level)
    root.handlers = [handler]

    for name in _UVICORN_LOGGERS:
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True
