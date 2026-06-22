"""Fase 5 — QualityScorer (Level-4) weighted filters, opt-in."""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.quality_scorer import QualityScorer
from app.services.config_resolver import ConfigResolver
from app.services.filter_pipeline import FilterPipeline
from app.models.normalized_signal import NormalizedSignal
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile

UTC = timezone.utc


def _sig(action="buy", price=5500.0, ts=None):
    return SimpleNamespace(
        action=action, price=price,
        signal_ts=ts or datetime(2026, 6, 22, 14, 30, tzinfo=UTC))  # 10:30 ET


def _bars_vol(last_vol, base=100, n=22):
    return [{"open": 5500, "high": 5505, "low": 5495, "close": 5500,
             "volume": (last_vol if i == n - 1 else base)} for i in range(n)]


def _flat_bars(price=5500.0, n=22):
    return [{"open": price, "high": price, "low": price, "close": price,
             "volume": 100} for _ in range(n)]


async def _score(filters, signal=None, bars=None):
    return await QualityScorer().score(
        signal or _sig(), bars if bars is not None else [],
        {"filters": filters, "timezone": "America/New_York"})


@pytest.mark.asyncio
async def test_no_filters_passthrough():
    assert await _score({}) == 100
    assert await _score({"volume_relative": {"enabled": False, "weight": 30}}) == 100


@pytest.mark.asyncio
async def test_volume_high_and_low():
    f = {"volume_relative": {"enabled": True, "weight": 30}}
    assert await _score(f, bars=_bars_vol(200)) == 100   # 2x avg → 1.0
    assert await _score(f, bars=_bars_vol(50)) == 0       # 0.5x avg → 0.0


@pytest.mark.asyncio
async def test_time_of_day_prime_vs_open():
    f = {"time_of_day": {"enabled": True, "weight": 20}}
    prime = await _score(f, signal=_sig(ts=datetime(2026, 6, 22, 14, 30, tzinfo=UTC)))
    openv = await _score(f, signal=_sig(ts=datetime(2026, 6, 22, 13, 40, tzinfo=UTC)))
    assert prime == 100   # 10:30 ET
    assert openv == 30     # 09:40 ET


@pytest.mark.asyncio
async def test_vwap_position_long():
    f = {"vwap_position": {"enabled": True, "weight": 25}}
    bars = _flat_bars(5500.0)  # vwap = 5500
    above = await _score(f, signal=_sig(action="buy", price=5527.5), bars=bars)
    below = await _score(f, signal=_sig(action="buy", price=5472.5), bars=bars)
    assert above == 100   # +0.5% above vwap
    assert below == 0     # -0.5% below vwap


@pytest.mark.asyncio
async def test_combined_weighting():
    f = {"volume_relative": {"enabled": True, "weight": 30},
         "time_of_day": {"enabled": True, "weight": 20}}
    # volume high (1.0) + open-hour (0.3): (30*1 + 20*0.3)/50 = 0.72 → 72
    s = await _score(f, signal=_sig(ts=datetime(2026, 6, 22, 13, 40, tzinfo=UTC)),
                     bars=_bars_vol(200))
    assert s == 72


@pytest.mark.asyncio
async def test_insufficient_bars_neutral():
    f = {"volume_relative": {"enabled": True, "weight": 30}}
    assert await _score(f, bars=_bars_vol(200, n=5)) == 50  # <21 bars → 0.5


@pytest.mark.asyncio
async def test_config_resolver_exposes_filters(db: AsyncSession):
    db.add(Strategy(strategy_id="qf", name="QF", asset_symbol="MES",
                    timeframe="5m", status="paper", enabled=True))
    db.add(StrategyProfile(strategy_id="qf", mode="paper", pipeline_config_json={
        "filters": {"volume_relative": {"enabled": True, "weight": 30}}}))
    await db.commit()
    c = await ConfigResolver().resolve(db, "qf", "MES")
    assert c["filters"]["volume_relative"]["enabled"] is True


@pytest.mark.asyncio
async def test_pipeline_blocks_on_low_score(db: AsyncSession, market_data_service):
    """Filters enabled + no bars (MockMD) → neutral 50 < score_minimum 70 → BLOCK."""
    signal = NormalizedSignal(
        raw_signal_id=uuid.uuid4(), strategy_id="qs", ticker_received="MES",
        mapped_symbol="MESU2026", action="buy", sentiment="long", price=5500.0,
        timeframe="5m", signal_ts=datetime.now(UTC), dedupe_key=uuid.uuid4().hex)
    strategy = Strategy(strategy_id="qs", name="QS", asset_symbol="MES",
                        status="live", enabled=True)
    config = {"mode": "normal", "score_minimum": 70,
              "filters": {"volume_relative": {"enabled": True, "weight": 30}}}
    result = await FilterPipeline(market_data_service).evaluate(
        db, signal, strategy, config)
    assert result.outcome == "BLOCK"
    assert result.block_reason == "score_below_minimum"
    assert result.block_level == 4
    assert result.score == 50
