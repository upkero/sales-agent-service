from contextvars import ContextVar
from uuid import uuid4

_request_id: ContextVar[str] = ContextVar("request_id", default="")


def get_request_id() -> str:
    return _request_id.get()


def generate_request_id() -> str:
    return str(uuid4())


def set_request_id(value: str) -> None:
    _request_id.set(value)
