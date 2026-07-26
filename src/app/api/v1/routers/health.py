import asyncio

from fastapi import APIRouter, HTTPException

from src.app.api.v1.dependencies import LLMClientDep, PricingGatewayDep

router = APIRouter(prefix="/health", tags=["infra"])


@router.get("/live")
async def liveness() -> dict[str, str]:
    """Is the process up. Nothing else — a liveness probe that checks a
    dependency restarts a healthy container because something else broke."""
    return {"status": "ok"}


@router.get("/ready")
async def readiness(llm_client: LLMClientDep, pricing: PricingGatewayDep) -> dict[str, str]:
    """Can this process actually do its job.

    Its job is to hold a selling conversation and quote a real price, which needs
    two things reachable: the language model and ops-core-api (the price source).
    Either one down means the agent cannot complete its core task, so readiness
    fails and the orchestrator keeps traffic away until it recovers. The two checks
    run concurrently — a readiness probe should be cheap and prompt.
    """
    llm_ok, core_ok = await asyncio.gather(llm_client.ping(), pricing.ping())
    down = [name for name, ok in (("llm", llm_ok), ("ops-core-api", core_ok)) if not ok]
    if down:
        raise HTTPException(status_code=503, detail=f"dependencies unavailable: {', '.join(down)}")
    return {"status": "ok"}
