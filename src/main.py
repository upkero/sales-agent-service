"""The HTTP process.

Thin by design: it builds the app, wires middleware and error handling, and hands
each turn to the SalesService. The dialogue logic lives in the services layer; a
router here never sees a prompt or a price.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.app.api.v1.exception_handlers import register_exception_handlers
from src.app.api.v1.middleware.rate_limit import register_rate_limiting
from src.app.api.v1.middleware.request_id import register_request_id_middleware
from src.app.api.v1.router import api_router
from src.app.api.v1.routers.health import router as health_router
from src.app.bootstrap.container import ApplicationContainer
from src.app.core.logging import setup_logging
from src.app.core.settings.app import get_app_settings
from src.app.core.settings.logging import get_logging_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    container = ApplicationContainer()
    app.state.container = container
    try:
        yield
    finally:
        await container.close()


def create_app() -> FastAPI:
    setup_logging(get_logging_settings())

    app = FastAPI(
        title="Sales Agent Service",
        version="0.1.0",
        description="A selling dialogue agent driven by an explicit stage machine, pricing via ops-core-api.",
        lifespan=lifespan,
    )

    settings = get_app_settings()

    # Middleware is registered inside-out: Starlette prepends each one, so the
    # LAST registered runs FIRST. The order below produces the runtime chain
    #     CORS -> request_id -> rate_limit -> routes
    # CORS has to be outermost because the rate limiter short-circuits with a
    # 429, and that response must travel back out through CORS or the browser
    # gets it without CORS headers — an opaque "Failed to fetch" instead of a
    # readable status. It also means an OPTIONS preflight is answered by CORS
    # before the limiter ever counts it, so a browser client no longer spends
    # half its quota on requests that carry no message.
    register_rate_limiting(app)
    register_request_id_middleware(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        # Explicit False: the API key travels in a header, never a cookie, so
        # there are no credentials to allow. Deriving it from the origin list
        # meant an empty list silently switched it on.
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)

    app.include_router(health_router)
    app.include_router(api_router)

    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True)
