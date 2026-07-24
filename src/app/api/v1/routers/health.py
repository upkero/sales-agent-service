from fastapi import APIRouter, HTTPException

from src.app.api.v1.dependencies import LLMClientDep

router = APIRouter(prefix="/health", tags=["infra"])


@router.get("/live")
async def liveness() -> dict[str, str]:
    """Is the process up. Nothing else — a liveness probe that checks a
    dependency restarts a healthy container because something else broke."""
    return {"status": "ok"}


@router.get("/ready")
async def readiness(llm_client: LLMClientDep) -> dict[str, str]:
    """Can this process do its job.

    Its job is holding a conversation, which it cannot do without the language
    model, so an unreachable model means not ready. ops-core-api is deliberately
    not probed here: a pricing outage degrades one stage, not the whole agent.
    """
    if not await llm_client.ping():
        raise HTTPException(status_code=503, detail="LLM unavailable")
    return {"status": "ok"}
