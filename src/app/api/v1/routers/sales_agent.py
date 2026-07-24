from fastapi import APIRouter

from src.app.api.v1.dependencies import SalesServiceDep
from src.app.schemas.sales import TurnRequest, TurnResponse

router = APIRouter(prefix="/sales-agent", tags=["sales-agent"])


@router.post("/turn", response_model=TurnResponse)
async def take_turn(body: TurnRequest, service: SalesServiceDep) -> TurnResponse:
    """Advance the conversation by one turn.

    Schema in, contract out: the request is parsed here, the service works in
    domain terms, and the outcome is mapped back to the response schema. The
    router knows nothing about stages, prompts or pricing.
    """
    outcome = await service.take_turn(body.conversation_id, body.message)
    return TurnResponse.from_outcome(outcome)
