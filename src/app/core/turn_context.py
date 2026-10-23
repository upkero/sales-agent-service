"""What a turn knows about itself, carried in ContextVars.

The stages, the LLM calls and the pricing gateway all run deep inside a turn and
none of them is handed its conversation id or a tally. A ContextVar is how a log
line written three layers down still names its conversation, and how the turn's
closing line adds up the LLM calls made on its behalf, without a parameter on
every signature between here and there.
"""

from contextvars import ContextVar
from dataclasses import dataclass

_conversation_id: ContextVar[str] = ContextVar("conversation_id", default="")


def get_conversation_id() -> str:
    return _conversation_id.get()


def set_conversation_id(value: str) -> None:
    # Never reset: an error raised out of the turn is logged by a handler that runs
    # after the turn has unwound, and that line is the one that needs the id most.
    # The context belongs to one request, so nothing leaks into the next.
    _conversation_id.set(value)


@dataclass(slots=True)
class TurnStats:
    """What one turn cost: how many model calls and how many tokens."""

    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


_stats: ContextVar[TurnStats] = ContextVar("turn_stats")


def start_turn_stats() -> TurnStats:
    stats = TurnStats()
    _stats.set(stats)
    return stats


def current_turn_stats() -> TurnStats:
    """The running turn's tally; a throwaway one for a call made outside a turn."""
    return _stats.get(None) or TurnStats()
