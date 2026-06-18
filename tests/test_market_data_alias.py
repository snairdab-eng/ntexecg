"""Per-symbol market-data alias (Anexo A.9.1; reglas 36, 38).

A micro contract reads the bridge data of its more-liquid parent (MES → ES).
The alias is read-only symbol substitution: it changes WHICH bridge files are
read for is_active (Level 1.6) and get_atr (Level 5) ONLY. It NEVER transforms
prices, and decisions / the TradersPost payload keep using the mapped contract.

All tests use MockMarketDataProvider or a tmp_path-backed bridge provider —
never real bridge files, never yfinance.
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.normalized_signal import NormalizedSignal
from app.models.strategy import Strategy
from app.models.symbol_map import SymbolMap
from app.services.filter_pipeline import FilterPipeline, PipelineResult
from app.services.market_data_service import (
    MarketDataService,
    NinjaTraderBridgeProvider,
)
from app.services.payload_builder import PayloadBuilder
from app.services.sl_tp_calculator import SLTPCalculator
from app.services.symbol_mapper import SymbolMapper

from tests.conftest import MockMarketDataProvider


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _add_symbol(
    db: AsyncSession,
    tv_symbol: str,
    mapped_symbol: str,
    market_data_symbol: str | None = None,
    active: bool = True,
) -> SymbolMap:
    sm = SymbolMap(
        tv_symbol=tv_symbol,
        mapped_symbol=mapped_symbol,
        market_data_symbol=market_data_symbol,
        exchange="CME",
        contract_type="futures_micro" if tv_symbol.startswith("M") else "futures_large",
        pine_script_config=f'"ticker": "{tv_symbol}"',
        active=active,
    )
    db.add(sm)
    await db.flush()
    return sm


def _make_signal(
    ticker_received: str = "MES",
    mapped_symbol: str | None = "MESU2026",
    action: str = "buy",
    sentiment: str = "long",
    price: float = 5500.0,
) -> NormalizedSignal:
    return NormalizedSignal(
        raw_signal_id=uuid.uuid4(),
        strategy_id="test_strat",
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


# ---------------------------------------------------------------------------
# resolve_market_data_symbol
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_micro_returns_parent(db: AsyncSession) -> None:
    """MES → ES (micro reuses parent bridge data)."""
    await _add_symbol(db, "MES", "MESU2026", market_data_symbol="ES")
    assert await SymbolMapper().resolve_market_data_symbol(db, "MES") == "ES"


@pytest.mark.asyncio
async def test_resolve_micro_yen_returns_parent(db: AsyncSession) -> None:
    """MJY → 6J."""
    await _add_symbol(db, "MJY", "MJYU2026", market_data_symbol="6J")
    assert await SymbolMapper().resolve_market_data_symbol(db, "MJY") == "6J"


@pytest.mark.asyncio
async def test_resolve_parent_returns_itself(db: AsyncSession) -> None:
    """ES → ES (parent has NULL alias → reads its own data)."""
    await _add_symbol(db, "ES", "ESU2026", market_data_symbol=None)
    assert await SymbolMapper().resolve_market_data_symbol(db, "ES") == "ES"


@pytest.mark.asyncio
async def test_resolve_large_fx_parent_returns_itself(db: AsyncSession) -> None:
    """6J → 6J (NULL alias)."""
    await _add_symbol(db, "6J", "6JU2026", market_data_symbol=None)
    assert await SymbolMapper().resolve_market_data_symbol(db, "6J") == "6J"


@pytest.mark.asyncio
async def test_resolve_unknown_returns_itself(db: AsyncSession) -> None:
    """XYZ is not in the table → resolves to itself (never None)."""
    assert await SymbolMapper().resolve_market_data_symbol(db, "XYZ") == "XYZ"


@pytest.mark.asyncio
async def test_resolve_empty_alias_falls_back_to_ticker(db: AsyncSession) -> None:
    """An empty-string alias behaves like NULL → use tv_symbol itself."""
    await _add_symbol(db, "ES", "ESU2026", market_data_symbol="")
    assert await SymbolMapper().resolve_market_data_symbol(db, "ES") == "ES"


# ---------------------------------------------------------------------------
# Level 1.6 — heartbeat read via the parent's bridge file (tmp_path)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_micro_is_active_via_parent_heartbeat(
    db: AsyncSession, tmp_path
) -> None:
    """Fresh heartbeat_ES.json → is_active('MES') True *via ES*, not via MES."""
    await _add_symbol(db, "MES", "MESU2026", market_data_symbol="ES")

    (tmp_path / "heartbeat_ES.json").write_text("{}", encoding="utf-8")
    svc = MarketDataService(NinjaTraderBridgeProvider(str(tmp_path)))

    data_symbol = await SymbolMapper().resolve_market_data_symbol(db, "MES")
    assert data_symbol == "ES"
    # Active through the parent's heartbeat; there is no heartbeat_MES.json.
    assert await svc.is_active("ES") is True
    assert await svc.is_active("MES") is False


@pytest.mark.asyncio
async def test_pipeline_micro_passes_l1_with_parent_heartbeat(
    db: AsyncSession, tmp_path
) -> None:
    """MES entry is NOT blocked at Level 1.6 when the parent heartbeat is fresh."""
    await _add_symbol(db, "MES", "MESU2026", market_data_symbol="ES")
    (tmp_path / "heartbeat_ES.json").write_text("{}", encoding="utf-8")
    svc = MarketDataService(NinjaTraderBridgeProvider(str(tmp_path)))
    pipeline = FilterPipeline(svc)

    result = await pipeline.evaluate(
        db, _make_signal(action="buy"), _make_strategy(), {"mode": "normal"}
    )

    assert not (result.outcome == "BLOCK" and result.block_level == 1)


@pytest.mark.asyncio
async def test_pipeline_micro_blocked_when_parent_heartbeat_removed(
    db: AsyncSession, tmp_path
) -> None:
    """Remove heartbeat_ES.json → MES entry BLOCKED at Level 1.6."""
    await _add_symbol(db, "MES", "MESU2026", market_data_symbol="ES")
    heartbeat = tmp_path / "heartbeat_ES.json"
    heartbeat.write_text("{}", encoding="utf-8")
    heartbeat.unlink()  # parent no longer broadcasting → micro must block

    svc = MarketDataService(NinjaTraderBridgeProvider(str(tmp_path)))
    pipeline = FilterPipeline(svc)

    result = await pipeline.evaluate(
        db, _make_signal(action="buy"), _make_strategy(), {"mode": "normal"}
    )

    assert result.outcome == "BLOCK"
    assert result.block_reason == "market_data_not_active"
    assert result.block_level == 1


# ---------------------------------------------------------------------------
# Pipeline reads ATR from the resolved data symbol
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_micro_reads_atr_from_parent(db: AsyncSession) -> None:
    """MES LONG with atr=8.0 → APPROVE, and get_atr was called with 'ES'."""
    from unittest.mock import patch

    await _add_symbol(db, "MES", "MESU2026", market_data_symbol="ES")
    provider = MockMarketDataProvider(atr=8.0)
    pipeline = FilterPipeline(MarketDataService(provider))
    config = {"mode": "normal", "score_minimum": 70, "sl_atr_multiplier": 1.5}

    with patch(
        "app.services.session_validator.SessionValidator.is_within_session_config",
        return_value=True,
    ):
        result = await pipeline.evaluate(
            db, _make_signal(action="buy", price=5500.0), _make_strategy(), config
        )

    assert result.outcome == "APPROVE"
    assert result.sl_price == 5500.0 - (8.0 * 1.5)  # alias never transforms price
    # The bridge was queried for the parent data symbol, not the micro/contract.
    assert "ES" in provider.get_atr_calls
    assert "MES" not in provider.get_atr_calls
    assert "MESU2026" not in provider.get_atr_calls
    assert "ES" in provider.is_active_calls


@pytest.mark.asyncio
async def test_pipeline_micro_reads_quality_bars_from_parent(db: AsyncSession) -> None:
    """Level 4 quality bars for a MES signal are read from the parent ('ES').

    Every bridge market-data read (is_active, quality bars, ATR) must go through
    resolve_market_data_symbol — never the micro or the mapped contract.
    """
    from unittest.mock import patch

    await _add_symbol(db, "MES", "MESU2026", market_data_symbol="ES")
    provider = MockMarketDataProvider(atr=8.0)
    pipeline = FilterPipeline(MarketDataService(provider))
    config = {"mode": "normal", "score_minimum": 70, "sl_atr_multiplier": 1.5}

    with patch(
        "app.services.session_validator.SessionValidator.is_within_session_config",
        return_value=True,
    ):
        result = await pipeline.evaluate(
            db, _make_signal(action="buy", price=5500.0), _make_strategy(), config
        )

    assert result.outcome == "APPROVE"
    # Quality bars (Level 4) queried the parent data symbol, not the micro/contract.
    assert "ES" in provider.get_bars_calls
    assert "MES" not in provider.get_bars_calls
    assert "MESU2026" not in provider.get_bars_calls


# ---------------------------------------------------------------------------
# SL price is identical for micro and parent (multiplier never enters SL path)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_micro_and_parent_same_sl_price() -> None:
    """MES SHORT and ES SHORT, same entry/ATR/multiplier → identical sl_price."""
    calc = SLTPCalculator()
    config = {"sl_atr_multiplier": 1.5, "tp_atr_multiplier": None}

    micro = _make_signal(ticker_received="MES", mapped_symbol="MESU2026", action="sell")
    parent = _make_signal(ticker_received="ES", mapped_symbol="ESU2026", action="sell")

    micro_res = await calc.calculate(micro, atr=8.0, entry_price=5500.0, config=config)
    parent_res = await calc.calculate(parent, atr=8.0, entry_price=5500.0, config=config)

    assert micro_res["passed"] is parent_res["passed"] is True
    assert micro_res["sl_price"] == parent_res["sl_price"] == 5500.0 + (8.0 * 1.5)


# ---------------------------------------------------------------------------
# Payload keeps the mapped contract symbol, NOT the data symbol
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_payload_carries_mapped_contract_not_data_symbol() -> None:
    """The alias is data-only: payload ticker stays the mapped contract (MESU2026)."""
    signal = _make_signal(ticker_received="MES", mapped_symbol="MESU2026", action="buy")
    result = PipelineResult(
        outcome="APPROVE", score=100, sl_price=5488.0, atr_value=8.0,
        market_data_provider="MockMarketDataProvider",
    )

    payload = PayloadBuilder().build(signal, _make_strategy(), {}, result)

    assert payload["ticker"] == "MESU2026"
    assert payload["ticker"] != "ES"
    assert payload["ticker"] != "MES"
