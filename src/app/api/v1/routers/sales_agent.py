from fastapi import APIRouter, Depends

from src.app.api.v1.dependencies import SalesServiceDep
from src.app.api.v1.dependencies.security import require_api_key
from src.app.schemas.sales import TurnRequest, TurnResponse

router = APIRouter(prefix="/sales-agent", tags=["sales-agent"])


# require_api_key gates the route when a key is configured (a no-op otherwise);
# the per-IP rate limit is enforced ahead of it, in middleware.
@router.post("/turn", response_model=TurnResponse, dependencies=[Depends(require_api_key)])
async def take_turn(body: TurnRequest, service: SalesServiceDep) -> TurnResponse:
    """Advance the conversation by one turn.

    Schema in, contract out: the request is parsed here, the service works in
    domain terms, and the outcome is mapped back to the response schema. The
    router knows nothing about stages, prompts or pricing.
    """
    outcome = await service.take_turn(body.conversation_id, body.message)
    return TurnResponse.from_outcome(outcome)
