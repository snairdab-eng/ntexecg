from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI

from app.core.config import settings
from app.core.logging import setup_logging
from app.api.health import router as health_router
from app.api.webhooks_luxalgo import router as webhooks_router
from app.web.routes_dashboard import router as dashboard_router
from app.web.routes_strategies import router as strategies_router
from app.web.routes_signals import router as signals_router
from app.web.routes_positions import router as positions_router
from app.web.routes_symbol_map import router as symbol_map_router
from app.web.routes_assets import router as assets_router
from app.web.routes_strategy_templates import router as templates_router
from app.web.routes_settings import router as settings_router
from app.web.routes_audit import router as audit_router


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
    app.include_router(strategies_router)
    app.include_router(signals_router)
    app.include_router(positions_router)
    app.include_router(symbol_map_router)
    app.include_router(assets_router)
    app.include_router(templates_router)
    app.include_router(settings_router)
    app.include_router(audit_router)
    return app


app = create_app()
