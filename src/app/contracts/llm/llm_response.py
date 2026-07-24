from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LLMResponse:
    content: str
    model: str | None = None
    finish_reason: str | None = None
    usage: Mapping[str, int] | None = None
    metadata: Mapping[str, object] | None = None
