"""Anexo 08 §2 — per-strategy guardrails (símbolo / temporalidad / antigüedad).

All guardrails are OPT-IN: with no enforcement flags the pipeline behaves
exactly as before (backward compatible). These tests use MockMarketDataProvider
(via the market_data_service fixture).
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.normalized_signal import NormalizedSignal
from app.models.strategy import Strategy
from app.services.filter_pipeline import FilterPipeline, _normalize_tf


def _make_signal(
    ticker_received: str = "MES",
    mapped_symbol: str | None = "MESU2025",
    action: str = "buy",
    sentiment: str = "long",
    timeframe: str | None = "5m",
    signal_ts: datetime | None = None,
) -> NormalizedSignal:
    return NormalizedSignal(
        raw_signal_id=uuid.uuid4(),
        strategy_id="test_strat",
        ticker_received=ticker_received,
        mapped_symbol=mapped_symbol,
        action=action,
        sentiment=sentiment,
        price=5500.0,
        timeframe=timeframe,
        signal_ts=signal_ts or datetime.now(timezone.utc),
        dedupe_key=uuid.uuid4().hex,
    )


def _make_strategy() -> Strategy:
    return Strategy(strategy_id="test_strat", name="Test",
                    asset_symbol="MES", status="live", enabled=True)


# ---------------------------------------------------------------------------
# _normalize_tf (pure helper)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("a,b", [
    ("5", "5m"), ("5m", "5"), ("15M", "15"), ("1h", "60"), ("4h", "240"),
])
def test_normalize_tf_equivalences(a, b):
    assert _normalize_tf(a) == _normalize_tf(b)


def test_normalize_tf_distinct():
    assert _normalize_tf("5m") != _normalize_tf("15m")
    assert _normalize_tf(None) is None


# ---------------------------------------------------------------------------
# 1.7 Symbol guardrail
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_symbol_mismatch_blocks(db: AsyncSession, market_data_service):
    signal = _make_signal(ticker_received="ES")  # wrong chart (ES vs MES)
    config = {"mode": "normal", "enforce_symbol_match": True,
              "expected_symbol": "MES"}
    result = await FilterPipeline(market_data_service).evaluate(
        db, signal, _make_strategy(), config)
    assert result.outcome == "BLOCK"
    assert result.block_reason == "symbol_mismatch"
    assert result.block_level == 1


@pytest.mark.asyncio
async def test_symbol_match_not_blocked_by_guardrail(db, market_data_service):
    signal = _make_signal(ticker_received="MES")
    config = {"mode": "normal", "enforce_symbol_match": True,
              "expected_symbol": "MES"}
    result = await FilterPipeline(market_data_service).evaluate(
        db, signal, _make_strategy(), config)
    assert result.block_reason != "symbol_mismatch"


@pytest.mark.asyncio
async def test_symbol_guardrail_disabled_by_default(db, market_data_service):
    """No enforcement flag → mismatched symbol does NOT trigger the guardrail."""
    signal = _make_signal(ticker_received="ES")
    config = {"mode": "normal"}  # no enforce_symbol_match
    result = await FilterPipeline(market_data_service).evaluate(
        db, signal, _make_strategy(), config)
    assert result.block_reason != "symbol_mismatch"


@pytest.mark.asyncio
async def test_symbol_guardrail_applies_to_exits(db, market_data_service):
    signal = _make_signal(ticker_received="ES", action="exit", sentiment="flat")
    config = {"mode": "normal", "enforce_symbol_match": True,
              "expected_symbol": "MES"}
    result = await FilterPipeline(market_data_service).evaluate(
        db, signal, _make_strategy(), config)
    assert result.outcome == "BLOCK"
    assert result.block_reason == "symbol_mismatch"


# ---------------------------------------------------------------------------
# 1.8 Timeframe guardrail
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_interval_mismatch_blocks(db, market_data_service):
    signal = _make_signal(timeframe="15")
    config = {"mode": "normal", "enforce_timeframe_match": True,
              "expected_timeframe": "5m"}
    result = await FilterPipeline(market_data_service).evaluate(
        db, signal, _make_strategy(), config)
    assert result.outcome == "BLOCK"
    assert result.block_reason == "interval_mismatch"
    assert result.block_level == 1


@pytest.mark.asyncio
async def test_interval_5_equals_5m(db, market_data_service):
    signal = _make_signal(timeframe="5")
    config = {"mode": "normal", "enforce_timeframe_match": True,
              "expected_timeframe": "5m"}
    result = await FilterPipeline(market_data_service).evaluate(
        db, signal, _make_strategy(), config)
    assert result.block_reason != "interval_mismatch"


# ---------------------------------------------------------------------------
# 2.0 Staleness guard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_entry_stale_blocks(db, market_data_service):
    ts = datetime(2026, 6, 19, 13, 0, 0, tzinfo=timezone.utc)
    signal = _make_signal(signal_ts=ts)
    config = {"mode": "normal", "signal_max_age_entry_seconds": 120,
              "now": ts + timedelta(seconds=200)}
    result = await FilterPipeline(market_data_service).evaluate(
        db, signal, _make_strategy(), config)
    assert result.outcome == "BLOCK"
    assert result.block_reason == "signal_stale"
    assert result.block_level == 2


@pytest.mark.asyncio
async def test_entry_fresh_not_stale(db, market_data_service):
    ts = datetime(2026, 6, 19, 13, 0, 0, tzinfo=timezone.utc)
    signal = _make_signal(signal_ts=ts)
    config = {"mode": "normal", "signal_max_age_entry_seconds": 120,
              "now": ts + timedelta(seconds=30)}
    result = await FilterPipeline(market_data_service).evaluate(
        db, signal, _make_strategy(), config)
    assert result.block_reason != "signal_stale"


@pytest.mark.asyncio
async def test_exit_staleness_skipped_when_no_exit_threshold(db, market_data_service):
    """Old exit + no exit threshold → never blocked by staleness."""
    ts = datetime(2026, 6, 19, 13, 0, 0, tzinfo=timezone.utc)
    signal = _make_signal(action="exit", sentiment="flat", signal_ts=ts)
    config = {"mode": "normal", "signal_max_age_entry_seconds": 120,
              "now": ts + timedelta(seconds=9999)}  # only entry threshold set
    result = await FilterPipeline(market_data_service).evaluate(
        db, signal, _make_strategy(), config)
    assert result.block_reason != "signal_stale"


@pytest.mark.asyncio
async def test_exit_stale_blocks_with_exit_threshold(db, market_data_service):
    ts = datetime(2026, 6, 19, 13, 0, 0, tzinfo=timezone.utc)
    signal = _make_signal(action="exit", sentiment="flat", signal_ts=ts)
    config = {"mode": "normal", "signal_max_age_exit_seconds": 300,
              "now": ts + timedelta(seconds=600)}
    result = await FilterPipeline(market_data_service).evaluate(
        db, signal, _make_strategy(), config)
    assert result.outcome == "BLOCK"
    assert result.block_reason == "signal_stale"
