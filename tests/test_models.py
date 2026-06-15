"""Model and repository tests.
All tests use SQLite in-memory via conftest.py fixtures.
Never use real PostgreSQL or real yfinance here.
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.decision import StrategyDecision
from app.models.global_profile import GlobalProfile
from app.models.market_data_status import MarketDataStatus
from app.models.normalized_signal import NormalizedSignal
from app.models.position_state import PositionState
from app.models.raw_signal import RawSignal
from app.models.strategy import Strategy
from app.models.strategy_performance import StrategyPerformance
from app.models.strategy_profile import StrategyProfile
from app.models.strategy_template import StrategyTemplate
from app.models.symbol_map import SymbolMap
from app.services.repositories import (
    create_audit_log,
    create_strategy,
    create_strategy_decision,
    get_active_symbol_map,
    get_asset_profile,
    get_global_profile,
    get_position_state,
    get_strategy_by_id,
    upsert_market_data_status,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures for reusable seed objects
# ---------------------------------------------------------------------------

async def _make_raw_signal(db: AsyncSession) -> RawSignal:
    rs = RawSignal(
        strategy_id="test_strat",
        ticker_received="MES",
        action="buy",
        sentiment="long",
        payload_json={"ticker": "MES", "action": "buy", "sentiment": "long"},
    )
    db.add(rs)
    await db.flush()
    return rs


async def _make_normalized_signal(db: AsyncSession, raw: RawSignal) -> NormalizedSignal:
    ns = NormalizedSignal(
        raw_signal_id=raw.id,
        strategy_id="test_strat",
        ticker_received="MES",
        mapped_symbol="MESU2025",
        action="buy",
        sentiment="long",
        timeframe="5m",
        signal_ts=_utcnow(),
        signal_role="entry_long",
        dedupe_key=str(uuid.uuid4()),
    )
    db.add(ns)
    await db.flush()
    return ns


async def _make_symbol_map(db: AsyncSession, tv_symbol: str, mapped: str) -> SymbolMap:
    sm = SymbolMap(
        tv_symbol=tv_symbol,
        mapped_symbol=mapped,
        exchange="CME",
        contract_type="futures_micro",
        pine_script_config=f'"ticker": "{tv_symbol}"',
        active=True,
    )
    db.add(sm)
    await db.flush()
    return sm


# ---------------------------------------------------------------------------
# Model insertion tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_raw_signal_insert(db: AsyncSession) -> None:
    rs = await _make_raw_signal(db)
    assert rs.id is not None
    assert rs.ticker_received == "MES"
    assert rs.token_valid is False


@pytest.mark.asyncio
async def test_normalized_signal_insert(db: AsyncSession) -> None:
    raw = await _make_raw_signal(db)
    ns = await _make_normalized_signal(db, raw)
    assert ns.id is not None
    assert ns.ticker_received == "MES"
    assert ns.mapped_symbol == "MESU2025"


@pytest.mark.asyncio
async def test_strategy_insert(db: AsyncSession) -> None:
    s = await create_strategy(db, "mes5m_test", "MES Test Strategy", "MES")
    assert s.strategy_id == "mes5m_test"
    assert s.status == "candidate"
    assert s.enabled is False


@pytest.mark.asyncio
async def test_strategy_unique_constraint(db: AsyncSession) -> None:
    await create_strategy(db, "unique_strat", "First", "MES")
    await db.commit()
    import sqlalchemy.exc
    with pytest.raises((sqlalchemy.exc.IntegrityError, Exception)):
        await create_strategy(db, "unique_strat", "Duplicate", "MNQ")
        await db.commit()


@pytest.mark.asyncio
async def test_normalized_signal_unique_dedupe_key(db: AsyncSession) -> None:
    raw = await _make_raw_signal(db)
    key = "fixed_dedupe_key_abc123"
    ns1 = NormalizedSignal(
        raw_signal_id=raw.id,
        strategy_id="s1",
        ticker_received="MES",
        action="buy",
        signal_ts=_utcnow(),
        dedupe_key=key,
    )
    db.add(ns1)
    await db.flush()
    await db.commit()

    import sqlalchemy.exc
    ns2 = NormalizedSignal(
        raw_signal_id=raw.id,
        strategy_id="s2",
        ticker_received="MES",
        action="sell",
        signal_ts=_utcnow(),
        dedupe_key=key,  # same key — must fail
    )
    db.add(ns2)
    with pytest.raises((sqlalchemy.exc.IntegrityError, Exception)):
        await db.flush()


@pytest.mark.asyncio
async def test_strategy_profile_insert(db: AsyncSession) -> None:
    await create_strategy(db, "sp_strat", "SP Strategy", "MES")
    sp = StrategyProfile(
        strategy_id="sp_strat",
        sl_atr_multiplier=1.5,
        mode="paper",
    )
    db.add(sp)
    await db.flush()
    assert sp.id is not None
    assert sp.dry_run is True


@pytest.mark.asyncio
async def test_strategy_performance_insert(db: AsyncSession) -> None:
    perf = StrategyPerformance(strategy_id="perf_strat")
    db.add(perf)
    await db.flush()
    assert perf.total_signals_received == 0
    assert perf.blocks_level_1 == 0


@pytest.mark.asyncio
async def test_global_profile_insert(db: AsyncSession) -> None:
    gp = GlobalProfile(mode="normal", dry_run=True, score_minimum=70)
    db.add(gp)
    await db.flush()
    assert gp.dry_run is True
    assert gp.traderspost_enabled is False


@pytest.mark.asyncio
async def test_strategy_decision_insert(db: AsyncSession) -> None:
    raw = await _make_raw_signal(db)
    ns = await _make_normalized_signal(db, raw)
    decision = await create_strategy_decision(
        db,
        normalized_signal_id=ns.id,
        strategy_id="test_strat",
        outcome="APPROVE",
        block_reason=None,
        score=100,
        sl_price=5484.0,
        atr_value=8.0,
    )
    assert decision.outcome == "APPROVE"
    assert decision.sl_price == 5484.0


@pytest.mark.asyncio
async def test_position_state_insert(db: AsyncSession) -> None:
    ps = PositionState(
        account_id="paper_1",
        symbol="MESU2025",
        state="LONG",
        state_source="estimated",
        entry_price=5500.0,
    )
    db.add(ps)
    await db.flush()
    assert ps.state_source == "estimated"


@pytest.mark.asyncio
async def test_audit_log_insert(db: AsyncSession) -> None:
    log = await create_audit_log(
        db,
        actor="system",
        action="CREATE",
        object_type="Strategy",
        object_id="mes5m_test",
    )
    assert log.actor == "system"


@pytest.mark.asyncio
async def test_market_data_status_upsert(db: AsyncSession) -> None:
    status = await upsert_market_data_status(
        db, "MES", provider="yfinance", is_active=False
    )
    assert status.symbol == "MES"
    assert status.is_active is False

    # Upsert again with updated data
    updated = await upsert_market_data_status(
        db, "MES", provider="yfinance", is_active=True, last_atr_5m=8.0
    )
    assert updated.is_active is True
    assert updated.last_atr_5m == 8.0


# ---------------------------------------------------------------------------
# Symbol Mapper tests
# CRITICAL: exact match only, no string manipulation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_symbol_map_mes(db: AsyncSession) -> None:
    await _make_symbol_map(db, "MES", "MESU2025")
    result = await get_active_symbol_map(db, "MES")
    assert result is not None
    assert result.mapped_symbol == "MESU2025"


@pytest.mark.asyncio
async def test_symbol_map_mnq(db: AsyncSession) -> None:
    await _make_symbol_map(db, "MNQ", "MNQU2025")
    result = await get_active_symbol_map(db, "MNQ")
    assert result is not None
    assert result.mapped_symbol == "MNQU2025"


@pytest.mark.asyncio
async def test_symbol_map_mjy(db: AsyncSession) -> None:
    await _make_symbol_map(db, "MJY", "MJYU2025")
    result = await get_active_symbol_map(db, "MJY")
    assert result is not None
    assert result.mapped_symbol == "MJYU2025"


@pytest.mark.asyncio
async def test_symbol_map_6j(db: AsyncSession) -> None:
    await _make_symbol_map(db, "6J", "6JU2025")
    result = await get_active_symbol_map(db, "6J")
    assert result is not None
    assert result.mapped_symbol == "6JU2025"


@pytest.mark.asyncio
async def test_symbol_map_m6j_does_not_exist(db: AsyncSession) -> None:
    # M6J is not a valid CME symbol — only MJY exists
    await _make_symbol_map(db, "MJY", "MJYU2025")
    result = await get_active_symbol_map(db, "M6J")
    assert result is None


@pytest.mark.asyncio
async def test_symbol_map_mes_with_bang_does_not_exist(db: AsyncSession) -> None:
    # "MES1!" is TradingView syntax — not a valid tv_symbol in our table
    await _make_symbol_map(db, "MES", "MESU2025")
    result = await get_active_symbol_map(db, "MES1!")
    assert result is None


@pytest.mark.asyncio
async def test_symbol_map_inactive_not_returned(db: AsyncSession) -> None:
    sm = SymbolMap(
        tv_symbol="OLD",
        mapped_symbol="OLDX2024",
        exchange="CME",
        contract_type="futures_micro",
        pine_script_config='"ticker": "OLD"',
        active=False,
    )
    db.add(sm)
    await db.flush()
    result = await get_active_symbol_map(db, "OLD")
    assert result is None


# ---------------------------------------------------------------------------
# Repository helper tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_strategy_by_id_found(db: AsyncSession) -> None:
    await create_strategy(db, "find_me", "Findable", "MES")
    result = await get_strategy_by_id(db, "find_me")
    assert result is not None
    assert result.name == "Findable"


@pytest.mark.asyncio
async def test_get_strategy_by_id_not_found(db: AsyncSession) -> None:
    result = await get_strategy_by_id(db, "ghost_strat")
    assert result is None


@pytest.mark.asyncio
async def test_get_global_profile(db: AsyncSession) -> None:
    db.add(GlobalProfile(mode="normal", dry_run=True))
    await db.flush()
    result = await get_global_profile(db)
    assert result is not None
    assert result.mode == "normal"


@pytest.mark.asyncio
async def test_get_position_state_default_flat(db: AsyncSession) -> None:
    ps = await get_position_state(db, "strat1", "paper_1", "MESU2025")
    assert ps.state == "FLAT"
    assert ps.state_source == "estimated"
    # Should not be persisted yet (no id)
    assert ps.id is None
