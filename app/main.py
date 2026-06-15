from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI

from app.core.config import settings
from app.core.logging import setup_logging
from app.api.health import router as health_router
from app.api.webhooks_luxalgo import router as webhooks_router
from app.web.routes_dashboard import router as dashboard_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    setup_logging(settings.LOG_LEVEL)

    from app.services.market_data_service import get_market_data_service
    from app.core.scheduler import HeartbeatMonitor

    market_data = get_market_data_service(settings)
    app.state.market_data = market_data

    monitor = HeartbeatMonitor(market_data)
    monitor.start()

    yield

    monitor.stop()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        lifespan=lifespan,
    )
    app.include_router(health_router)
    app.include_router(webhooks_router)
    app.include_router(dashboard_router)
    return app


app = create_app()
