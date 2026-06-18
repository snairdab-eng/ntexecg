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

from app.db.base import Base
from app.db.session import get_db
from app.main import create_app

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

_test_engine = create_async_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
)
_TestSessionLocal = async_sessionmaker(
    _test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


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
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with _TestSessionLocal() as session:
        yield session
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture(scope="function")
async def client(db: AsyncSession) -> AsyncClient:
    app = create_app()

    async def _override_get_db() -> AsyncSession:
        yield db

    app.dependency_overrides[get_db] = _override_get_db

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
def _clear_symbol_mapper_cache() -> None:
    """Reset module-level SymbolMapper cache before every test.

    The cache is module-level with 5-minute TTL for production use.
    Tests create fresh SQLite DBs each function, so stale cache entries
    from a previous test would return wrong results.
    """
    import app.services.symbol_mapper as _sm
    _sm.clear_cache()
