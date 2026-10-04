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

    Tries three things in order of cleanliness: the whole string, the string with
    a ```json fence stripped, then the first brace-balanced span. Quote-aware so a
    brace inside a string value does not end the scan early.
    """
    candidates = (raw.strip(), _strip_fence(raw), _first_balanced_object(raw))
    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _strip_fence(raw: str) -> str:
    text = raw.strip()
    if not text.startswith("```"):
        return ""
    # Drop the opening fence line (``` or ```json) and any trailing fence.
    without_open = text.split("\n", 1)[1] if "\n" in text else ""
    return without_open.rsplit("```", 1)[0].strip()


def _first_balanced_object(raw: str) -> str:
    start = raw.find("{")
    if start == -1:
        return ""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(raw)):
        char = raw[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return raw[start : index + 1]
    return ""
