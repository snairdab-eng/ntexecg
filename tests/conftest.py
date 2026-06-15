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
    async def get_bars(self, *args, **kwargs) -> list:
        return []

    async def get_atr(self, *args, **kwargs) -> float:
        return 8.0

    async def is_active(self, symbol: str) -> bool:
        return True


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


@pytest.fixture(autouse=True)
def _clear_symbol_mapper_cache() -> None:
    """Reset module-level SymbolMapper cache before every test.

    The cache is module-level with 5-minute TTL for production use.
    Tests create fresh SQLite DBs each function, so stale cache entries
    from a previous test would return wrong results.
    """
    import app.services.symbol_mapper as _sm
    _sm.clear_cache()
