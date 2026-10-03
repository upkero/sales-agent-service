"""The sales state machine, defined as data.

The stages and the legal moves between them live here — in the innermost layer,
importing nothing from services — so they are the single source of truth that the
stages, the orchestrator and the tests all agree on.
"""

from dataclasses import dataclass
from enum import StrEnum


class SalesStage(StrEnum):
    GREETING = "greeting"
    QUALIFY = "qualify"
    PRESENT = "present"
    OBJECTION_HANDLING = "objection_handling"
    UPSELL = "upsell"
    CLOSE = "close"


# The legal moves. Every stage's route() must return a member of its own row;
# SalesService rejects anything else as a bug (InvalidStageTransitionError).
# Two edges are not a step forward: PRESENT -> UPSELL, the sanctioned single skip
# past objection handling when the prospect raised no objection, and the way back
# to PRESENT from OBJECTION_HANDLING and UPSELL, taken only when the prospect
# changed the quantity or the service and the new order has to be priced.
ALLOWED_TRANSITIONS: dict[SalesStage, frozenset[SalesStage]] = {
    SalesStage.GREETING: frozenset({SalesStage.GREETING, SalesStage.QUALIFY}),
    SalesStage.QUALIFY: frozenset({SalesStage.QUALIFY, SalesStage.PRESENT}),
    SalesStage.PRESENT: frozenset(
        {SalesStage.PRESENT, SalesStage.OBJECTION_HANDLING, SalesStage.UPSELL}
    ),
    SalesStage.OBJECTION_HANDLING: frozenset(
        {SalesStage.OBJECTION_HANDLING, SalesStage.UPSELL, SalesStage.PRESENT}
    ),
    SalesStage.UPSELL: frozenset({SalesStage.UPSELL, SalesStage.CLOSE, SalesStage.PRESENT}),
    SalesStage.CLOSE: frozenset({SalesStage.CLOSE}),
}


@dataclass(frozen=True, slots=True)
class StageResult:
    """What a stage hands back: what to say, and where the machine goes next.

    `current_stage` is the stage that produced the reply; `next_stage` is where
    the conversation will be on the following turn. They differ exactly when the
    stage advanced.
    """

    reply: str
    current_stage: SalesStage
    next_stage: SalesStage
    handoff: bool = False


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    """The result of one turn as the caller cares about it: what to say, which
    stage the conversation is now in, and the id needed to continue it. The
    service builds this so the router never has to reach into the conversation."""

    conversation_id: str
    reply: str
    stage: SalesStage
    handoff: bool

    @property
    def done(self) -> bool:
        return self.stage is SalesStage.CLOSE
