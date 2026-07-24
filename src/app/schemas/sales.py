from pydantic import BaseModel, Field

from src.app.contracts.sales import SalesStage, TurnOutcome


class TurnRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000, description="The customer's message this turn.")
    conversation_id: str | None = Field(
        default=None,
        max_length=100,
        description="Omit on the first turn; pass the id returned previously to continue a conversation.",
    )


class TurnResponse(BaseModel):
    """Everything a frontend needs to render the turn and light up the funnel.

    `stage` is the stage the conversation is now in — the one the progress bar
    should highlight — not the stage that produced the reply.
    """

    conversation_id: str
    reply: str
    stage: SalesStage
    done: bool
    handoff: bool

    @classmethod
    def from_outcome(cls, outcome: TurnOutcome) -> "TurnResponse":
        return cls(
            conversation_id=outcome.conversation_id,
            reply=outcome.reply,
            stage=outcome.stage,
            done=outcome.done,
            handoff=outcome.handoff,
        )
