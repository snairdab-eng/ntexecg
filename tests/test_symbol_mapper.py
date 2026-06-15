"""SymbolMapper tests.

CRITICAL invariants:
  - Lookup is exact-match only: "mes" ≠ "MES", "MES1!" ≠ "MES"
  - "M6J" → None  (Micro Yen is MJY, not M6J)
  - inactive rows are never returned
  - cache does not bleed between tests (conftest autouse fixture clears it)
"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.symbol_map import SymbolMap
from app.services.symbol_mapper import SymbolMapper


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _add_symbol(
    db: AsyncSession,
    tv_symbol: str,
    mapped_symbol: str,
    active: bool = True,
) -> SymbolMap:
    sm = SymbolMap(
        tv_symbol=tv_symbol,
        mapped_symbol=mapped_symbol,
        exchange="CME",
        contract_type="futures_micro",
        pine_script_config=f'"ticker": "{tv_symbol}"',
        active=active,
    )
    db.add(sm)
    await db.flush()
    return sm


# ---------------------------------------------------------------------------
# Positive lookups (symbols that exist and are active)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mes(db: AsyncSession) -> None:
    await _add_symbol(db, "MES", "MESU2025")
    result = await SymbolMapper().map_symbol(db, "MES")
    assert result == "MESU2025"


@pytest.mark.asyncio
async def test_mnq(db: AsyncSession) -> None:
    await _add_symbol(db, "MNQ", "MNQU2025")
    result = await SymbolMapper().map_symbol(db, "MNQ")
    assert result == "MNQU2025"


@pytest.mark.asyncio
async def test_mym(db: AsyncSession) -> None:
    await _add_symbol(db, "MYM", "MYMU2025")
    result = await SymbolMapper().map_symbol(db, "MYM")
    assert result == "MYMU2025"


@pytest.mark.asyncio
async def test_m2k(db: AsyncSession) -> None:
    await _add_symbol(db, "M2K", "M2KU2025")
    result = await SymbolMapper().map_symbol(db, "M2K")
    assert result == "M2KU2025"


@pytest.mark.asyncio
async def test_mjy(db: AsyncSession) -> None:
    await _add_symbol(db, "MJY", "MJYU2025")
    result = await SymbolMapper().map_symbol(db, "MJY")
    assert result == "MJYU2025"


@pytest.mark.asyncio
async def test_m6e(db: AsyncSession) -> None:
    await _add_symbol(db, "M6E", "M6EU2025")
    result = await SymbolMapper().map_symbol(db, "M6E")
    assert result == "M6EU2025"


@pytest.mark.asyncio
async def test_6j(db: AsyncSession) -> None:
    await _add_symbol(db, "6J", "6JU2025")
    result = await SymbolMapper().map_symbol(db, "6J")
    assert result == "6JU2025"


@pytest.mark.asyncio
async def test_6e(db: AsyncSession) -> None:
    await _add_symbol(db, "6E", "6EU2025")
    result = await SymbolMapper().map_symbol(db, "6E")
    assert result == "6EU2025"


# ---------------------------------------------------------------------------
# Negative lookups (must return None)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lowercase_mes_returns_none(db: AsyncSession) -> None:
    """Case-sensitive: 'mes' is not the same as 'MES'."""
    await _add_symbol(db, "MES", "MESU2025")
    result = await SymbolMapper().map_symbol(db, "mes")
    assert result is None


@pytest.mark.asyncio
async def test_mes_with_bang_returns_none(db: AsyncSession) -> None:
    """'MES1!' is TradingView's continuous contract syntax — not in our table."""
    await _add_symbol(db, "MES", "MESU2025")
    result = await SymbolMapper().map_symbol(db, "MES1!")
    assert result is None


@pytest.mark.asyncio
async def test_m6j_returns_none(db: AsyncSession) -> None:
    """M6J does not exist on CME. Micro Yen is MJY, not M6J.
    NTEXECG must NOT guess or infer — no mapping means None.
    """
    await _add_symbol(db, "MJY", "MJYU2025")
    result = await SymbolMapper().map_symbol(db, "M6J")
    assert result is None


@pytest.mark.asyncio
async def test_unknown_symbol_returns_none(db: AsyncSession) -> None:
    result = await SymbolMapper().map_symbol(db, "XYZ")
    assert result is None


@pytest.mark.asyncio
async def test_inactive_symbol_returns_none(db: AsyncSession) -> None:
    """Inactive mappings (rolled contract) must be invisible."""
    await _add_symbol(db, "OLD", "OLDX2024", active=False)
    result = await SymbolMapper().map_symbol(db, "OLD")
    assert result is None


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cache_hit_returns_same_result(db: AsyncSession) -> None:
    await _add_symbol(db, "MES", "MESU2025")
    mapper = SymbolMapper()
    first = await mapper.map_symbol(db, "MES")
    second = await mapper.map_symbol(db, "MES")
    assert first == second == "MESU2025"


@pytest.mark.asyncio
async def test_cache_is_per_key(db: AsyncSession) -> None:
    await _add_symbol(db, "MES", "MESU2025")
    await _add_symbol(db, "MNQ", "MNQU2025")
    mapper = SymbolMapper()
    assert await mapper.map_symbol(db, "MES") == "MESU2025"
    assert await mapper.map_symbol(db, "MNQ") == "MNQU2025"
    assert await mapper.map_symbol(db, "M6J") is None


# ---------------------------------------------------------------------------
# pine_script_config
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_pine_script_config(db: AsyncSession) -> None:
    await _add_symbol(db, "MJY", "MJYU2025")
    config = await SymbolMapper().get_pine_script_config(db, "MJY")
    assert config == '"ticker": "MJY"'


@pytest.mark.asyncio
async def test_get_pine_script_config_none_for_unknown(db: AsyncSession) -> None:
    config = await SymbolMapper().get_pine_script_config(db, "NOPE")
    assert config is None
