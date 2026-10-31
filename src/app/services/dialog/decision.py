"""Parsing the model's per-turn control output, defined once for every stage.

The stages ask the model for a JSON object `{"reply": ..., "data": {...}}`: the
prose to say and the structured signals the state machine routes on. Small models
occasionally break that contract, so parsing is lenient about *how* the JSON is
wrapped but strict about *what* counts as valid — an unparseable turn returns
None, and the base stage turns repeated Nones into a bounded hand-off rather than
letting the machine act on a guess.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_DECODER = json.JSONDecoder()


@dataclass(frozen=True, slots=True)
class AgentDecision:
    """The two halves of a turn: what to say, and the signals to route on."""

    reply: str
    data: Mapping[str, Any]

    @classmethod
    def parse(cls, raw: str) -> "AgentDecision | None":
        obj = extract_json_object(raw)
        if obj is None:
            return None

        reply = obj.get("reply")
        if not isinstance(reply, str) or not reply.strip():
            # A control object with no sayable reply is useless to the caller; treat
            # it as a parse failure so the escalation path handles it uniformly.
            return None

        data = obj.get("data")
        return cls(reply=reply.strip(), data=data if isinstance(data, dict) else {})

    def flag(self, name: str) -> bool:
        """A boolean signal from `data`, tolerant of the model sending 'true'/'yes'."""
        value = self.data.get(name)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"true", "yes", "1"}
        return False


def extract_json_object(raw: str) -> dict[str, Any] | None:
    """Best-effort recovery of the JSON object from a model reply.

    `raw_decode` reads one object from the first brace and ignores what follows, so a
    ```json fence, a lead-in sentence or trailing prose all fall away. It is string-aware,
    so a brace inside a value does not end the object early. Only the first brace span is
    tried: if it is not valid JSON, the reply is a parse failure.
    """
    start = raw.find("{")
    if start == -1:
        return None
    try:
        obj, _ = _DECODER.raw_decode(raw, start)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None
