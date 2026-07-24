from fastapi import APIRouter

from src.app.api.v1.routers.sales_agent import router as sales_agent_router

# Top-level v1 router. Feature routers are included here; health lives outside
# /api/v1 because probes should not move when the API version does.
api_router = APIRouter(prefix="/api/v1")
api_router.include_router(sales_agent_router)
