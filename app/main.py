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
from app.web.routes_lab import router as lab_router
from app.web.routes_riesgo import router as riesgo_router
from app.web.routes_positions import router as positions_router
from app.web.routes_portfolio import router as portfolio_router
from app.web.routes_symbol_map import router as symbol_map_router
from app.web.routes_assets import router as assets_router
from app.web.routes_api import router as api_router
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


# SEC-1 Tarea 4 — CSP HONESTA y funcional. Compromisos documentados en script-src:
#  · 'unsafe-inline' — Tailwind CDN inyecta estilos inline y las plantillas usan
#    JS inline (banner del token, Alpine/HTMX).
#  · 'unsafe-eval' — SEC-1c: el build ESTÁNDAR de Alpine evalúa las expresiones
#    de x-data/x-show con `new Function` (≈ eval); sin esto Alpine NO inicializa
#    los componentes (bug: el modal de confirmación quedaba abierto y ningún
#    botón respondía). Alternativa futura: el build "CSP" de Alpine (evita eval)
#    — exige reescribir plantillas a métodos declarados; se ANOTA, no se hace.
# Los 3 CDN van explícitos. `frame-ancestors 'self'` (enmienda): L6 embebe
# /ui/lab en un iframe SAME-ORIGIN — 'self' lo permite; nadie más enmarca el
# panel. Candidato futuro: self-host de Tailwind para quitar 'unsafe-inline'/CDN.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.tailwindcss.com "
    "https://unpkg.com https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com "
    "https://fonts.googleapis.com; "
    "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; "
    "frame-src 'self'; frame-ancestors 'self'; base-uri 'self'; "
    "form-action 'self'; object-src 'none'"
)


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        lifespan=lifespan,
    )
    app.add_exception_handler(NotAuthenticated, _redirect_to_login)

    @app.middleware("http")
    async def _security(request: Request, call_next):
        from fastapi.responses import PlainTextResponse
        # SEC-1 Tarea 3 — fail-fast del SESSION_SECRET: en NO-test, un secreto
        # < 32 bytes es forjable → las rutas /ui responden 503 (en test se
        # permite corto para no romper la suite — gate por entorno).
        if (request.url.path.startswith("/ui")
                and settings.APP_ENV != "test"
                and len(settings.SESSION_SECRET or "") < 32):
            return PlainTextResponse(
                "config insegura: SESSION_SECRET débil (< 32 bytes) — genera "
                "uno fuerte y reinicia.", status_code=503)

        resp = await call_next(request)
        # SEC-1 Tarea 4 — headers de seguridad.
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        if "text/html" in resp.headers.get("content-type", ""):
            resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
            resp.headers.setdefault("Referrer-Policy", "same-origin")
            resp.headers.setdefault("Content-Security-Policy", _CSP)
            proto = (request.headers.get("x-forwarded-proto")
                     or request.url.scheme)
            if proto == "https":
                resp.headers.setdefault(
                    "Strict-Transport-Security",
                    "max-age=31536000; includeSubDomains")
        return resp

    # Public / exempt routers (no auth)
    app.include_router(health_router)
    app.include_router(webhooks_router)
    app.include_router(auth_router)

    # Protected web UI — require_auth applied to every route in these routers
    protected = [Depends(require_auth)]
    app.include_router(dashboard_router, dependencies=protected)
    app.include_router(strategies_router, dependencies=protected)
    app.include_router(signals_router, dependencies=protected)
    app.include_router(lab_router, dependencies=protected)
    app.include_router(riesgo_router, dependencies=protected)
    app.include_router(positions_router, dependencies=protected)
    app.include_router(portfolio_router, dependencies=protected)
    app.include_router(symbol_map_router, dependencies=protected)
    app.include_router(assets_router, dependencies=protected)
    app.include_router(api_router, dependencies=protected)
    # P3-1: Templates deprecado (NO destructivo) — la UI se retiró; el modelo
    # StrategyTemplate y la columna strategies.template_id se CONSERVAN.
    app.include_router(settings_router, dependencies=protected)
    app.include_router(audit_router, dependencies=protected)
    return app


app = create_app()
