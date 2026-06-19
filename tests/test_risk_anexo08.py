"""Anexo 08 §2/§4 — Level-3 per-operation risk gates (opt-in).

  - 3.4 qty_exceeds_max     (quantity > max_contracts)
  - 3.5 stop_required       (stop mandatory but no stop_ticks)
  - 3.6 risk_exceeds_max    (quantity * stop_ticks * tick_value > max USD)

All gates are entries-only (exits skip Level 3) and disabled unless the
config provides the corresponding key. Uses MockMarketDataProvider.
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.normalized_signal import NormalizedSignal
from app.models.strategy import Strategy
from app.services.filter_pipeline import FilterPipeline


def _signal(quantity: int = 1, action: str = "buy", sentiment: str = "long"):
    return NormalizedSignal(
        raw_signal_id=uuid.uuid4(),
        strategy_id="test_strat",
        ticker_received="MES",
        mapped_symbol="MESU2026",
        action=action,
        sentiment=sentiment,
        price=5500.0,
        quantity=quantity,
        timeframe="5m",
        signal_ts=datetime.now(timezone.utc),
        dedupe_key=uuid.uuid4().hex,
    )


def _strategy():
    return Strategy(strategy_id="test_strat", name="Test",
                    asset_symbol="MES", status="live", enabled=True)


# ---------------------------------------------------------------------------
# 3.4 Max contracts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_qty_exceeds_max_blocks(db: AsyncSession, market_data_service):
    signal = _signal(quantity=5)
    config = {"mode": "normal", "max_contracts": 2}
    result = await FilterPipeline(market_data_service).evaluate(
        db, signal, _strategy(), config)
    assert result.outcome == "BLOCK"
    assert result.block_reason == "qty_exceeds_max"
    assert result.block_level == 3


@pytest.mark.asyncio
async def test_qty_within_max_not_blocked(db, market_data_service):
    signal = _signal(quantity=2)
    config = {"mode": "normal", "max_contracts": 2}
    result = await FilterPipeline(market_data_service).evaluate(
        db, signal, _strategy(), config)
    assert result.block_reason != "qty_exceeds_max"


@pytest.mark.asyncio
async def test_qty_gate_disabled_by_default(db, market_data_service):
    signal = _signal(quantity=99)
    config = {"mode": "normal"}  # no max_contracts
    result = await FilterPipeline(market_data_service).evaluate(
        db, signal, _strategy(), config)
    assert result.block_reason != "qty_exceeds_max"


# ---------------------------------------------------------------------------
# 3.5 Mandatory stop
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stop_required_blocks_when_missing(db, market_data_service):
    signal = _signal()
    config = {"mode": "normal", "stop_required": True}  # no stop_ticks
    result = await FilterPipeline(market_data_service).evaluate(
        db, signal, _strategy(), config)
    assert result.outcome == "BLOCK"
    assert result.block_reason == "stop_required"
    assert result.block_level == 3


@pytest.mark.asyncio
async def test_stop_required_ok_when_present(db, market_data_service):
    signal = _signal()
    config = {"mode": "normal", "stop_required": True, "stop_ticks": 20}
    result = await FilterPipeline(market_data_service).evaluate(
        db, signal, _strategy(), config)
    assert result.block_reason != "stop_required"


# ---------------------------------------------------------------------------
# 3.6 Dollar risk per operation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_risk_usd_exceeds_max_blocks(db, market_data_service):
    # 2 contracts * 40 ticks * $1.25 = $100 > $50 max
    signal = _signal(quantity=2)
    config = {"mode": "normal", "stop_ticks": 40, "tick_value": 1.25,
              "risk_usd_max_operation": 50}
    result = await FilterPipeline(market_data_service).evaluate(
        db, signal, _strategy(), config)
    assert result.outcome == "BLOCK"
    assert result.block_reason == "risk_exceeds_max"
    assert result.block_level == 3


@pytest.mark.asyncio
async def test_risk_usd_within_max_not_blocked(db, market_data_service):
    # 1 contract * 20 ticks * $1.25 = $25 <= $50 max
    signal = _signal(quantity=1)
    config = {"mode": "normal", "stop_ticks": 20, "tick_value": 1.25,
              "risk_usd_max_operation": 50}
    result = await FilterPipeline(market_data_service).evaluate(
        db, signal, _strategy(), config)
    assert result.block_reason != "risk_exceeds_max"


@pytest.mark.asyncio
async def test_risk_gate_skipped_without_tick_value(db, market_data_service):
    """No tick_value → dollar-risk gate cannot compute → skipped (not blocked)."""
    signal = _signal(quantity=99)
    config = {"mode": "normal", "stop_ticks": 40,
              "risk_usd_max_operation": 1}  # tick_value missing
    result = await FilterPipeline(market_data_service).evaluate(
        db, signal, _strategy(), config)
    assert result.block_reason != "risk_exceeds_max"


# ---------------------------------------------------------------------------
# Exits bypass Level 3 entirely (risk gates never apply to exits)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_exit_bypasses_risk_gates(db, market_data_service):
    signal = _signal(quantity=99, action="exit", sentiment="flat")
    config = {"mode": "normal", "max_contracts": 1, "stop_required": True}
    result = await FilterPipeline(market_data_service).evaluate(
        db, signal, _strategy(), config)
    assert result.block_reason not in ("qty_exceeds_max", "stop_required",
                                       "risk_exceeds_max")
