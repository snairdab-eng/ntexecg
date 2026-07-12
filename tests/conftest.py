import os

# ---------------------------------------------------------------------------
# Test isolation — MUST run before any `app.*` import.
#
# app.core.config.settings is a module-level singleton built at import time.
# These env vars (env vars outrank the env_file in pydantic-settings, and
# APP_ENV=test redirects config to .env.test) guarantee the suite NEVER loads
# the production .env: webhook token, salt, DRY_RUN and the DB are deterministic
# no matter which host pytest runs on.
# ---------------------------------------------------------------------------
os.environ["APP_ENV"] = "test"
os.environ["LUXALGO_WEBHOOK_SECRET"] = "dev_global_token"
os.environ["WEBHOOK_TOKEN_SALT"] = "dev_salt_change_in_production_min_32_chars"
os.environ["DRY_RUN"] = "true"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import create_app

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


class MockMarketDataProvider:
    """In-memory provider for tests — never reads real bridge files or yfinance.

    Records the symbols it is queried with so tests can assert the market-data
    alias resolved correctly (e.g. a MES signal must query the bridge as "ES").
    Defaults (atr=8.0, active=True) keep every existing test green.
    """

    def __init__(self, atr: float | None = 8.0, active: bool = True) -> None:
        self._atr = atr
        self._active = active
        self.get_atr_calls: list[str] = []
        self.get_bars_calls: list[str] = []
        self.is_active_calls: list[str] = []

    async def get_bars(self, symbol: str = "", *args, **kwargs) -> list:
        self.get_bars_calls.append(symbol)
        return []

    async def get_atr(self, symbol: str = "", *args, **kwargs) -> float | None:
        self.get_atr_calls.append(symbol)
        return self._atr

    async def is_active(self, symbol: str = "", *args, **kwargs) -> bool:
        self.is_active_calls.append(symbol)
        return self._active


@pytest_asyncio.fixture(scope="function")
async def db() -> AsyncSession:
    # A fresh engine PER TEST, bound to this test's own event loop (pytest-asyncio
    # function loop scope). StaticPool keeps the single in-memory SQLite connection
    # shared between create_all and the session. The finally block closes the
    # session and disposes the engine — which stops aiosqlite's connection worker
    # thread. Without dispose(), that thread (and its connection) lingers and the
    # async teardown hangs because the event loop can never finalize cleanly.
    engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session = session_factory()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def app(db: AsyncSession):
    """The FastAPI app wired to the test DB. Exposed so tests can set app.state
    (e.g. app.state.market_data) — the ASGITransport does not run the lifespan
    that would normally populate it."""
    application = create_app()

    async def _override_get_db() -> AsyncSession:
        yield db

    application.dependency_overrides[get_db] = _override_get_db
    return application


@pytest_asyncio.fixture(scope="function")
async def client(app) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


@pytest.fixture
def mock_market_data() -> MockMarketDataProvider:
    return MockMarketDataProvider()


@pytest.fixture
def market_data_service(mock_market_data: MockMarketDataProvider):
    """Wrap MockMarketDataProvider in MarketDataService for pipeline tests."""
    from app.services.market_data_service import MarketDataService

    return MarketDataService(mock_market_data)


@pytest.fixture(autouse=True)
def _reset_security_state() -> None:
    """SEC-1 — el lockout del login y el watermark de revocación son estado
    EN MEMORIA a nivel módulo; sin esto se filtrarían entre tests (un test con
    10 fallos bloquearía el login de otro)."""
    from app.core import auth as _auth, login_guard as _lg, token_once as _to
    _lg.reset()
    _auth._reset_revocation()
    _to.reset()
    yield
    _lg.reset()
    _auth._reset_revocation()
    _to.reset()


@pytest.fixture(autouse=True)
def _clear_symbol_mapper_cache() -> None:
    """Reset module-level SymbolMapper cache before every test.

    The cache is module-level with 5-minute TTL for production use.
    Tests create fresh SQLite DBs each function, so stale cache entries
    from a previous test would return wrong results.
    """
    import app.services.symbol_mapper as _sm
    _sm.clear_cache()


@pytest.fixture(autouse=True)
def _fast_lab_recalc(monkeypatch) -> None:
    """LAB-1 — el upload (Lab y Riesgo) encadena un recalc en 2º plano, que en
    producción es un subproceso real de scripts.lab_analyze. En la suite eso
    arrancaría el analizador PESADO contra el repo REAL (lento y con efectos);
    aquí se sustituye por un subproceso trivial e instantáneo — el mecanismo
    JOBS/polling sigue siendo real. Los tests que verifican el job (o quieren
    su propio comando) monkeypatchean _recalc_cmd y sobrescriben este default.
    """
    import sys
    import app.web.routes_lab as _rl

    monkeypatch.setattr(
        _rl, "_recalc_cmd",
        lambda key, is_strategy: [sys.executable, "-c", "pass"])


@pytest.fixture(autouse=True)
def _isolate_models_dir(tmp_path, monkeypatch) -> None:
    """Point MODELS_DIR at a fresh empty temp dir per test.

    Prevents any on-disk trained HMM model from leaking into tests (baseline
    get_regime tests must stay deterministic). Also clears the trainer cache.
    """
    from app.core.config import settings as _settings
    import app.services.hmm_trainer as _hmm

    monkeypatch.setattr(_settings, "MODELS_DIR", str(tmp_path / "models"))
    _hmm._CACHE.clear()
