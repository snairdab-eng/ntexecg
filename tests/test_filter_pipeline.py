"""FilterPipeline tests (Level 1-5 evaluation).

All tests use MockMarketDataProvider (get_atr→8.0, is_active→True).
Pipeline is fail-fast: stops at first failing level.
Exits get special handling: bypass levels 2,3,4,5, always permitted.
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.normalized_signal import NormalizedSignal
from app.models.strategy import Strategy
from app.services.filter_pipeline import FilterPipeline


def _utcnow():
    return datetime.now(timezone.utc)


def _make_signal(
    strategy_id: str = "test_strat",
    ticker_received: str = "MES",
    mapped_symbol: str | None = "MESU2025",
    action: str = "buy",
    sentiment: str = "long",
    price: float = 5500.0,
) -> NormalizedSignal:
    return NormalizedSignal(
        raw_signal_id=uuid.uuid4(),
        strategy_id=strategy_id,
        ticker_received=ticker_received,
        mapped_symbol=mapped_symbol,
        action=action,
        sentiment=sentiment,
        price=price,
        signal_ts=_utcnow(),
        dedupe_key=uuid.uuid4().hex,
    )


def _make_strategy(status: str = "live") -> Strategy:
    return Strategy(
        strategy_id="test_strat",
        name="Test",
        asset_symbol="MES",
        status=status,
        enabled=True,
    )


@pytest.mark.asyncio
async def test_level_1_symbol_not_mapped(
    db: AsyncSession, market_data_service
) -> None:
    """Level 1.4: mapped_symbol=None → BLOCK symbol_not_mapped."""
    signal = _make_signal(mapped_symbol=None)
    strategy = _make_strategy()
    config = {"mode": "normal"}
    pipeline = FilterPipeline(market_data_service)

    result = await pipeline.evaluate(db, signal, strategy, config)

    assert result.outcome == "BLOCK"
    assert result.block_reason == "symbol_not_mapped"
    assert result.block_level == 1


@pytest.mark.asyncio
async def test_level_1_strategy_candidate(
    db: AsyncSession, market_data_service
) -> None:
    """Level 1.2: strategy.status=candidate → QUEUE_FOR_REVIEW."""
    signal = _make_signal()
    strategy = _make_strategy(status="candidate")
    config = {"mode": "normal"}
    pipeline = FilterPipeline(market_data_service)

    result = await pipeline.evaluate(db, signal, strategy, config)

    assert result.outcome == "QUEUE_FOR_REVIEW"
    assert result.block_level is None


@pytest.mark.asyncio
async def test_level_1_strategy_retired(
    db: AsyncSession, market_data_service
) -> None:
    """Level 1.2: strategy.status=retired → BLOCK."""
    signal = _make_signal()
    strategy = _make_strategy(status="retired")
    config = {"mode": "normal"}
    pipeline = FilterPipeline(market_data_service)

    result = await pipeline.evaluate(db, signal, strategy, config)

    assert result.outcome == "BLOCK"
    assert result.block_reason == "strategy_retired"
    assert result.block_level == 1


@pytest.mark.asyncio
async def test_level_1_global_mode_paused_for_entry(
    db: AsyncSession, market_data_service
) -> None:
    """Level 1.1: global mode=paused + entry → BLOCK.

    NX-01: the brake lives under "global_mode" — "mode" carries the strategy's
    maturity mode (paper/micro/...) after the StrategyProfile merge.
    """
    signal = _make_signal(action="buy")
    strategy = _make_strategy()
    config = {"global_mode": "paused"}
    pipeline = FilterPipeline(market_data_service)

    result = await pipeline.evaluate(db, signal, strategy, config)

    assert result.outcome == "BLOCK"
    assert result.block_reason == "global_paused"
    assert result.block_level == 1


@pytest.mark.asyncio
async def test_level_1_global_mode_paused_for_exit_permitted(
    db: AsyncSession, market_data_service
) -> None:
    """Level 1.1: global mode=paused + exit → permitted (exits bypass)."""
    signal = _make_signal(action="exit")
    strategy = _make_strategy()
    config = {"global_mode": "paused"}
    pipeline = FilterPipeline(market_data_service)

    # Level 1 passes (exit exception), but pipeline needs full eval
    # For this test, just verify level 1 logic doesn't block exits
    result = await pipeline.evaluate(db, signal, strategy, config)

    # Exit signals pass through levels (may APPROVE or skip levels 2-5)
    assert result.outcome != "BLOCK" or result.block_level != 1


@pytest.mark.asyncio
async def test_level_2_outside_session_for_entry(
    db: AsyncSession, market_data_service
) -> None:
    """Level 2.2: time outside trading window + entry → BLOCK."""
    signal = _make_signal(action="buy")
    strategy = _make_strategy()
    config = {
        "mode": "normal",
        "session_config_json": {
            "timezone": "America/New_York",
            "days_enabled": [1, 2, 3, 4, 5],
            "entry_start": "09:30",
            "entry_end": "15:45",
        },
    }
    pipeline = FilterPipeline(market_data_service)

    # Mock time to 02:00 ET (outside session)
    with patch(
        "app.services.session_validator.datetime"
    ) as mock_dt:
        from datetime import datetime as real_dt
        from zoneinfo import ZoneInfo

        et = ZoneInfo("America/New_York")
        mock_dt.now.return_value = real_dt(
            2026, 6, 15, 2, 0, 0, tzinfo=et
        )  # 02:00 ET
        mock_dt.side_effect = lambda *args, **kwargs: real_dt(*args, **kwargs)

        result = await pipeline.evaluate(db, signal, strategy, config)

    assert result.outcome == "BLOCK"
    assert result.block_reason == "outside_session_hours"
    assert result.block_level == 2


@pytest.mark.asyncio
async def test_level_4_score_100_passes(
    db: AsyncSession, market_data_service
) -> None:
    """Level 4: score=100 >= minimum=70 → passes (Phase 1 stub)."""
    signal = _make_signal(action="buy")
    strategy = _make_strategy()
    config = {"mode": "normal", "score_minimum": 70}
    pipeline = FilterPipeline(market_data_service)

    with patch(
        "app.services.session_validator.SessionValidator.is_within_session_config",
        return_value=True,
    ):
        result = await pipeline.evaluate(db, signal, strategy, config)

    # Should pass level 4 (score=100)
    assert result.score == 100


@pytest.mark.asyncio
async def test_level_5_atr_available_calculates_sl(
    db: AsyncSession, market_data_service
) -> None:
    """Level 5: ATR available → APPROVE with sl_price."""
    signal = _make_signal(action="buy", price=5500.0)
    strategy = _make_strategy()
    config = {
        "mode": "normal",
        "score_minimum": 70,
        "sl_atr_multiplier": 1.5,
        "atr_period": 14,
    }
    pipeline = FilterPipeline(market_data_service)

    with patch(
        "app.services.session_validator.SessionValidator.is_within_session_config",
        return_value=True,
    ):
        result = await pipeline.evaluate(db, signal, strategy, config)

    # Mock ATR=8.0, entry=5500 → SL=5500-(8*1.5)=5488
    assert result.outcome == "APPROVE"
    assert result.block_level is None
    assert result.sl_price is not None


@pytest.mark.asyncio
async def test_level_5_atr_none_blocks(
    db: AsyncSession, market_data_service
) -> None:
    """Level 5: ATR=None → BLOCK atr_calculation_failed."""
    signal = _make_signal(action="buy")
    strategy = _make_strategy()
    config = {"mode": "normal", "score_minimum": 70, "sl_atr_multiplier": 1.5}
    pipeline = FilterPipeline(market_data_service)

    # Mock market data service get_atr to return None
    async def _mock_get_atr(*args, **kwargs):
        return None

    market_data_service.get_atr = _mock_get_atr

    with patch(
        "app.services.session_validator.SessionValidator.is_within_session_config",
        return_value=True,
    ):
        result = await pipeline.evaluate(db, signal, strategy, config)

    assert result.outcome == "BLOCK"
    assert result.block_reason == "atr_calculation_failed"
    assert result.block_level == 5


@pytest.mark.asyncio
async def test_exit_skips_levels_2_3_4_5(
    db: AsyncSession, market_data_service
) -> None:
    """EXIT signals skip levels 2, 3, 4, 5 (or get marked 'skipped')."""
    signal = _make_signal(action="exit")
    strategy = _make_strategy()
    config = {"mode": "normal"}
    pipeline = FilterPipeline(market_data_service)

    result = await pipeline.evaluate(db, signal, strategy, config)

    # Exit should pass level 1 and skip others
    assert "skipped" in str(result.pipeline_execution_json.get("level_2", {})) or \
           "skipped" in str(result.pipeline_execution_json.get("level_3", {}))


@pytest.mark.asyncio
async def test_pipeline_execution_json_recorded(
    db: AsyncSession, market_data_service
) -> None:
    """Full audit trail recorded in pipeline_execution_json."""
    signal = _make_signal(action="buy")
    strategy = _make_strategy()
    config = {"mode": "normal", "score_minimum": 70, "sl_atr_multiplier": 1.5}
    pipeline = FilterPipeline(market_data_service)

    with patch(
        "app.services.session_validator.SessionValidator.is_within_session_config",
        return_value=True,
    ):
        result = await pipeline.evaluate(db, signal, strategy, config)

    # pipeline_execution_json should have keys for each level
    assert "level_1" in result.pipeline_execution_json
    assert "level_2" in result.pipeline_execution_json or \
           result.outcome == "BLOCK"  # May block before level 2


@pytest.mark.asyncio
async def test_happy_path_entry_approve_with_sl(
    db: AsyncSession, market_data_service
) -> None:
    """Happy path: entry signal → APPROVE with sl_price calculated."""
    signal = _make_signal(action="buy", price=5500.0)
    strategy = _make_strategy(status="live")
    config = {
        "mode": "normal",
        "score_minimum": 70,
        "sl_atr_multiplier": 1.5,
        "session_config_json": {
            "timezone": "America/New_York",
            "days_enabled": [1, 2, 3, 4, 5],
            "entry_start": "09:30",
            "entry_end": "15:45",
        },
    }
    pipeline = FilterPipeline(market_data_service)

    with patch(
        "app.services.session_validator.SessionValidator.is_within_session_config",
        return_value=True,
    ):
        result = await pipeline.evaluate(db, signal, strategy, config)

    assert result.outcome == "APPROVE"
    assert result.block_reason is None
    assert result.block_level is None
    assert result.score == 100
    assert result.sl_price is not None
    assert isinstance(result.sl_price, float)


# ---------------------------------------------------------------------------
# Level 1.6 — Market data active (NinjaTrader bridge heartbeat)
# Contract rule 25: NT inactive → BLOCK entries, PERMIT exits
# ---------------------------------------------------------------------------

class _InactiveMarketData:
    """MockMarketDataProvider variant where is_active returns False."""

    async def get_bars(self, *a, **kw) -> list:
        return []

    async def get_atr(self, *a, **kw) -> float:
        return 8.0

    async def is_active(self, symbol: str) -> bool:
        return False


@pytest.mark.asyncio
async def test_level_1_bridge_inactive_blocks_entry(db: AsyncSession) -> None:
    """Level 1.6: bridge inactive + entry → BLOCK market_data_not_active."""
    from app.services.market_data_service import MarketDataService

    signal = _make_signal(action="buy")
    strategy = _make_strategy(status="live")
    config = {"mode": "normal"}
    svc = MarketDataService(_InactiveMarketData())
    pipeline = FilterPipeline(svc)

    result = await pipeline.evaluate(db, signal, strategy, config)

    assert result.outcome == "BLOCK"
    assert result.block_reason == "market_data_not_active"
    assert result.block_level == 1


@pytest.mark.asyncio
async def test_level_1_bridge_inactive_permits_exit(db: AsyncSession) -> None:
    """Level 1.6: bridge inactive + exit → permitted (exits always prioritized)."""
    from app.services.market_data_service import MarketDataService

    signal = _make_signal(action="exit")
    strategy = _make_strategy(status="live")
    config = {"mode": "normal"}
    svc = MarketDataService(_InactiveMarketData())
    pipeline = FilterPipeline(svc)

    result = await pipeline.evaluate(db, signal, strategy, config)

    # Exit must NOT be blocked at level 1 due to inactive bridge
    assert not (result.outcome == "BLOCK" and result.block_level == 1)
    assert result.outcome == "APPROVE"


# ---------------------------------------------------------------------------
# Level 3.3 — Position state (contract rule 11: uncertain → block entries)
# ---------------------------------------------------------------------------

async def _make_position(db: AsyncSession, symbol: str, state: str) -> None:
    from app.models.position_state import PositionState

    ps = PositionState(
        account_id="paper_default",
        symbol=symbol,
        strategy_id="test_strat",
        state=state,
        state_source="estimated",
    )
    db.add(ps)
    await db.flush()


@pytest.mark.asyncio
async def test_level_3_unknown_position_blocks_entry(
    db: AsyncSession, market_data_service
) -> None:
    """Level 3.3: UNKNOWN position state + entry → BLOCK unknown_position_state."""
    await _make_position(db, "MESU2025", "UNKNOWN")

    signal = _make_signal(action="buy")
    strategy = _make_strategy(status="live")
    config = {"mode": "normal", "account_id": "paper_default"}
    pipeline = FilterPipeline(market_data_service)

    result = await pipeline.evaluate(db, signal, strategy, config)

    assert result.outcome == "BLOCK"
    assert result.block_reason == "unknown_position_state"
    assert result.block_level == 3


@pytest.mark.asyncio
async def test_level_3_unknown_position_permits_exit(
    db: AsyncSession, market_data_service
) -> None:
    """Level 3.3: UNKNOWN position state + exit → passes (exits skip level 3)."""
    await _make_position(db, "MESU2025", "UNKNOWN")

    signal = _make_signal(action="exit")
    strategy = _make_strategy(status="live")
    config = {"mode": "normal", "account_id": "paper_default"}
    pipeline = FilterPipeline(market_data_service)

    result = await pipeline.evaluate(db, signal, strategy, config)

    # Exit bypasses level 3 entirely
    assert result.pipeline_execution_json["level_3"].get("skipped") is True
    assert result.outcome == "APPROVE"
    # Exits skip level 5 → no SL calculated
    assert result.sl_price is None


# ---------------------------------------------------------------------------
# Level 4 — Market regime gate (Fase 6, opt-in)
# ---------------------------------------------------------------------------

_REGIME_CFG = {
    "mode": "normal", "score_minimum": 70, "sl_atr_multiplier": 1.5,
    "regime": {"enabled": True, "timeframe": "1h",
               "allowed_regimes": ["trending_bull"]},
}


@pytest.mark.asyncio
async def test_regime_gate_blocks_when_not_allowed(
    db: AsyncSession, market_data_service
) -> None:
    """Regime enabled + current regime not in allowed_regimes → BLOCK (level 4)."""
    signal = _make_signal(action="buy")
    strategy = _make_strategy(status="live")
    pipeline = FilterPipeline(market_data_service)
    with patch(
        "app.services.session_validator.SessionValidator.is_within_session_config",
        return_value=True,
    ), patch(
        "app.services.hmm_service.HMMService.get_regime",
        new=AsyncMock(return_value="trending_bear"),
    ):
        result = await pipeline.evaluate(db, signal, strategy, dict(_REGIME_CFG))
    assert result.outcome == "BLOCK"
    assert result.block_reason == "regime_not_allowed"
    assert result.block_level == 4
    assert result.pipeline_execution_json["regime"]["regime"] == "trending_bear"


@pytest.mark.asyncio
async def test_regime_gate_passes_when_allowed(
    db: AsyncSession, market_data_service
) -> None:
    """Regime enabled + current regime allowed → not blocked by the gate."""
    signal = _make_signal(action="buy")
    strategy = _make_strategy(status="live")
    pipeline = FilterPipeline(market_data_service)
    with patch(
        "app.services.session_validator.SessionValidator.is_within_session_config",
        return_value=True,
    ), patch(
        "app.services.hmm_service.HMMService.get_regime",
        new=AsyncMock(return_value="trending_bull"),
    ):
        result = await pipeline.evaluate(db, signal, strategy, dict(_REGIME_CFG))
    assert result.outcome == "APPROVE"
    assert result.pipeline_execution_json["regime"]["regime"] == "trending_bull"


@pytest.mark.asyncio
async def test_regime_gate_fails_open_on_unknown(
    db: AsyncSession, market_data_service
) -> None:
    """Regime 'unknown' (insufficient data) never blocks — fail-open."""
    signal = _make_signal(action="buy")
    strategy = _make_strategy(status="live")
    pipeline = FilterPipeline(market_data_service)
    with patch(
        "app.services.session_validator.SessionValidator.is_within_session_config",
        return_value=True,
    ), patch(
        "app.services.hmm_service.HMMService.get_regime",
        new=AsyncMock(return_value="unknown"),
    ):
        result = await pipeline.evaluate(db, signal, strategy, dict(_REGIME_CFG))
    assert result.outcome == "APPROVE"


@pytest.mark.asyncio
async def test_regime_gate_disabled_skips(
    db: AsyncSession, market_data_service
) -> None:
    """No regime config → gate skipped, no 'regime' key in the execution trace."""
    signal = _make_signal(action="buy")
    strategy = _make_strategy(status="live")
    config = {"mode": "normal", "score_minimum": 70, "sl_atr_multiplier": 1.5}
    pipeline = FilterPipeline(market_data_service)
    with patch(
        "app.services.session_validator.SessionValidator.is_within_session_config",
        return_value=True,
    ):
        result = await pipeline.evaluate(db, signal, strategy, config)
    assert result.outcome == "APPROVE"
    assert "regime" not in result.pipeline_execution_json
