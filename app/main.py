from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse

from app.core.config import settings
from app.core.logging import setup_logging
from app.core.auth_middleware import NotAuthenticated, require_auth
from app.api.health import router as health_router
from app.api.webhooks_luxalgo import router as webhooks_router
from app.api.auth_routes import router as auth_router
from app.web.routes_dashboard import router as dashboard_router
from app.web.routes_strategies import router as strategies_router
from app.web.routes_signals import router as signals_router
from app.web.routes_analytics import router as analytics_router
from app.web.routes_positions import router as positions_router
from app.web.routes_symbol_map import router as symbol_map_router
from app.web.routes_assets import router as assets_router
from app.web.routes_api import router as api_router
from app.web.routes_strategy_templates import router as templates_router
from app.web.routes_settings import router as settings_router
from app.web.routes_audit import router as audit_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    setup_logging(settings.LOG_LEVEL)

    from app.services.market_data_service import get_market_data_service
    from app.core.scheduler import (
        HeartbeatMonitor, ExitManagerJob, MarketBarsUpdater, HMMTrainerJob,
    )

    market_data = get_market_data_service(settings)
    app.state.market_data = market_data

    monitor = HeartbeatMonitor(market_data)
    monitor.start()

    exit_manager = ExitManagerJob(settings)
    exit_manager.start()

    bars_updater = MarketBarsUpdater(
        market_data, interval_minutes=settings.MARKET_BARS_UPDATE_MINUTES
    )
    bars_updater.start()

    hmm_trainer_job = HMMTrainerJob(settings)
    hmm_trainer_job.start()

    yield

    monitor.stop()
    exit_manager.stop()
    bars_updater.stop()
    hmm_trainer_job.stop()


async def _redirect_to_login(request: Request, exc: NotAuthenticated) -> RedirectResponse:
    """Turn an auth failure into a redirect to the login page."""
    return RedirectResponse(url="/ui/login", status_code=303)


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        lifespan=lifespan,
    )
    app.add_exception_handler(NotAuthenticated, _redirect_to_login)

    # Public / exempt routers (no auth)
    app.include_router(health_router)
    app.include_router(webhooks_router)
    app.include_router(auth_router)

    # Protected web UI — require_auth applied to every route in these routers
    protected = [Depends(require_auth)]
    app.include_router(dashboard_router, dependencies=protected)
    app.include_router(strategies_router, dependencies=protected)
    app.include_router(signals_router, dependencies=protected)
    app.include_router(analytics_router, dependencies=protected)
    app.include_router(positions_router, dependencies=protected)
    app.include_router(symbol_map_router, dependencies=protected)
    app.include_router(assets_router, dependencies=protected)
    app.include_router(api_router, dependencies=protected)
    app.include_router(templates_router, dependencies=protected)
    app.include_router(settings_router, dependencies=protected)
    app.include_router(audit_router, dependencies=protected)
    return app


app = create_app()
