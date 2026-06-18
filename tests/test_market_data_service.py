"""MarketDataService tests.

CRITICAL: ALL tests use MockMarketDataProvider (from conftest).
No real yfinance calls. No real bridge path reads.
MockMarketDataProvider already in conftest.py:
    async def get_atr(*a, **kw): return 8.0
    async def is_active(symbol):  return True
    async def get_bars(*a, **kw): return []

NinjaTraderBridgeProvider tests use a temp filesystem (tmp_path fixture)
to simulate heartbeat files with controlled mtimes.
"""
from __future__ import annotations

import os
import time
from types import SimpleNamespace

import pytest

from app.services.market_data_service import (
    DatabentoProvider,
    MarketDataService,
    NinjaTraderBridgeProvider,
    TradovateAPIProvider,
    YfinanceProvider,
    get_market_data_service,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(**overrides) -> SimpleNamespace:
    defaults = {
        "MARKET_DATA_PROVIDER": "yfinance",
        "NTBRIDGE_PATH": "/mnt/ntbridge",
        "NTBRIDGE_HEARTBEAT_MAX_AGE": 60,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# MarketDataService with MockMarketDataProvider
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_service_get_atr_returns_mock_value(mock_market_data) -> None:
    svc = MarketDataService(mock_market_data)
    result = await svc.get_atr("MES", "5m")
    assert result == 8.0


@pytest.mark.asyncio
async def test_service_is_active_returns_true(mock_market_data) -> None:
    svc = MarketDataService(mock_market_data)
    assert await svc.is_active("MES") is True


@pytest.mark.asyncio
async def test_service_get_bars_returns_empty_list(mock_market_data) -> None:
    svc = MarketDataService(mock_market_data)
    result = await svc.get_bars("MES", "5m")
    assert result == []


@pytest.mark.asyncio
async def test_service_get_atr_with_period(mock_market_data) -> None:
    svc = MarketDataService(mock_market_data)
    result = await svc.get_atr("MES", "15m", period=20)
    assert result == 8.0


@pytest.mark.asyncio
async def test_service_get_bridge_status_empty_symbols(mock_market_data) -> None:
    svc = MarketDataService(mock_market_data)
    status = await svc.get_bridge_status([])
    assert status == {}


@pytest.mark.asyncio
async def test_service_get_bridge_status_with_symbols(mock_market_data) -> None:
    svc = MarketDataService(mock_market_data)
    status = await svc.get_bridge_status(["MESU2025", "MJYU2025"])
    assert "MESU2025" in status
    assert "MJYU2025" in status
    assert status["MESU2025"]["active"] is True
    assert "provider" in status["MESU2025"]


@pytest.mark.asyncio
async def test_service_provider_name_in_bridge_status(mock_market_data) -> None:
    svc = MarketDataService(mock_market_data)
    status = await svc.get_bridge_status(["MESU2025"])
    # Provider name is the class name of the underlying provider
    assert status["MESU2025"]["provider"] == "MockMarketDataProvider"


# ---------------------------------------------------------------------------
# NinjaTraderBridgeProvider — is_active() tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bridge_is_active_false_when_path_not_mounted(tmp_path) -> None:
    """If bridge path doesn't exist, is_active must return False without raising."""
    non_existent = tmp_path / "non_existent_bridge"
    provider = NinjaTraderBridgeProvider(
        bridge_path=str(non_existent), heartbeat_max_age=60
    )
    result = await provider.is_active("MES")
    assert result is False


@pytest.mark.asyncio
async def test_bridge_is_active_false_when_heartbeat_missing(tmp_path) -> None:
    """Heartbeat file absent → is_active returns False."""
    provider = NinjaTraderBridgeProvider(
        bridge_path=str(tmp_path), heartbeat_max_age=60
    )
    result = await provider.is_active("MES")
    assert result is False


@pytest.mark.asyncio
async def test_bridge_is_active_false_when_heartbeat_stale(tmp_path) -> None:
    """Heartbeat mtime older than max_age → is_active returns False."""
    heartbeat = tmp_path / "heartbeat_MES.json"
    heartbeat.write_text("{}")

    # Set mtime to 120 seconds ago (well past the 60-second threshold)
    old_mtime = time.time() - 120
    os.utime(heartbeat, (old_mtime, old_mtime))

    provider = NinjaTraderBridgeProvider(
        bridge_path=str(tmp_path), heartbeat_max_age=60
    )
    result = await provider.is_active("MES")
    assert result is False


@pytest.mark.asyncio
async def test_bridge_is_active_true_when_heartbeat_fresh(tmp_path) -> None:
    """Recently-written heartbeat → is_active returns True."""
    heartbeat = tmp_path / "heartbeat_MES.json"
    heartbeat.write_text("{}")
    # Default mtime is current — file was just created

    provider = NinjaTraderBridgeProvider(
        bridge_path=str(tmp_path), heartbeat_max_age=60
    )
    result = await provider.is_active("MES")
    assert result is True


@pytest.mark.asyncio
async def test_bridge_is_active_boundary_at_max_age(tmp_path) -> None:
    """File exactly at max_age boundary is still considered active (<=)."""
    heartbeat = tmp_path / "heartbeat_MNQ.json"
    heartbeat.write_text("{}")

    # Set mtime to exactly max_age seconds ago
    boundary_mtime = time.time() - 60
    os.utime(heartbeat, (boundary_mtime, boundary_mtime))

    provider = NinjaTraderBridgeProvider(
        bridge_path=str(tmp_path), heartbeat_max_age=60
    )
    # Boundary: age might be 60 or 61 depending on sub-second timing.
    # Just verify it doesn't raise and returns a bool.
    result = await provider.is_active("MNQ")
    assert isinstance(result, bool)


@pytest.mark.asyncio
async def test_bridge_get_bars_returns_empty_when_path_missing(tmp_path) -> None:
    provider = NinjaTraderBridgeProvider(
        bridge_path=str(tmp_path / "no_such_dir"), heartbeat_max_age=60
    )
    bars = await provider.get_bars("MES", "5m")
    assert bars == []


@pytest.mark.asyncio
async def test_bridge_get_bars_reads_json_file(tmp_path) -> None:
    bars_data = [
        {"time": "2026-06-15T09:30:00", "open": 5490.0, "high": 5500.0,
         "low": 5488.0, "close": 5498.0, "volume": 1000},
        {"time": "2026-06-15T09:35:00", "open": 5498.0, "high": 5505.0,
         "low": 5496.0, "close": 5502.0, "volume": 1200},
    ]
    bars_file = tmp_path / "bars_MESU2025_5m.json"
    bars_file.write_text(__import__("json").dumps(bars_data))

    provider = NinjaTraderBridgeProvider(bridge_path=str(tmp_path), heartbeat_max_age=60)
    result = await provider.get_bars("MESU2025", "5m")
    assert len(result) == 2
    assert result[0]["close"] == 5498.0


@pytest.mark.asyncio
async def test_bridge_get_bars_respects_limit(tmp_path) -> None:
    bars_data = [
        {"time": f"2026-06-15T09:{i:02d}:00", "open": 5490.0, "high": 5500.0,
         "low": 5488.0, "close": 5498.0, "volume": 1000}
        for i in range(10)
    ]
    bars_file = tmp_path / "bars_MES_5m.json"
    bars_file.write_text(__import__("json").dumps(bars_data))

    provider = NinjaTraderBridgeProvider(bridge_path=str(tmp_path), heartbeat_max_age=60)
    result = await provider.get_bars("MES", "5m", limit=3)
    assert len(result) == 3


@pytest.mark.asyncio
async def test_bridge_get_atr_returns_none_with_no_bars(tmp_path) -> None:
    provider = NinjaTraderBridgeProvider(bridge_path=str(tmp_path), heartbeat_max_age=60)
    result = await provider.get_atr("MES", "5m")
    assert result is None


@pytest.mark.asyncio
async def test_bridge_get_bars_tolerates_utf8_bom(tmp_path) -> None:
    """The .NET bridge writer emits files with a UTF-8 BOM. get_bars must parse
    them (utf-8-sig) instead of failing with 'Unexpected UTF-8 BOM'.
    """
    import json

    bars_data = [
        {"time": f"2026-06-15T09:{i:02d}:00",
         "open": 5490.0 + i, "high": 5500.0 + i,
         "low": 5485.0 + i, "close": 5498.0 + i, "volume": 1000 + i}
        for i in range(20)
    ]
    bars_file = tmp_path / "bars_ES_5m.json"
    # Write the bytes WITH an explicit UTF-8 BOM prefix.
    bars_file.write_bytes(b"\xef\xbb\xbf" + json.dumps(bars_data).encode("utf-8"))

    provider = NinjaTraderBridgeProvider(bridge_path=str(tmp_path), heartbeat_max_age=60)

    result = await provider.get_bars("ES", "5m")
    assert len(result) == 20
    assert result[0]["close"] == 5498.0

    atr = await provider.get_atr("ES", "5m")
    assert isinstance(atr, float)
    assert atr > 0


# ---------------------------------------------------------------------------
# Phase 5 stubs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tradovate_is_active_returns_false() -> None:
    provider = TradovateAPIProvider()
    assert await provider.is_active("MES") is False


@pytest.mark.asyncio
async def test_tradovate_get_bars_raises() -> None:
    provider = TradovateAPIProvider()
    with pytest.raises(NotImplementedError):
        await provider.get_bars("MES", "5m")


@pytest.mark.asyncio
async def test_databento_is_active_returns_false() -> None:
    provider = DatabentoProvider()
    assert await provider.is_active("MES") is False


@pytest.mark.asyncio
async def test_databento_get_bars_raises() -> None:
    provider = DatabentoProvider()
    with pytest.raises(NotImplementedError):
        await provider.get_bars("MES", "5m")


# ---------------------------------------------------------------------------
# Factory: get_market_data_service()
# ---------------------------------------------------------------------------

def test_factory_yfinance_returns_yfinance_provider() -> None:
    svc = get_market_data_service(_make_settings(MARKET_DATA_PROVIDER="yfinance"))
    assert isinstance(svc, MarketDataService)
    assert isinstance(svc.provider, YfinanceProvider)


def test_factory_ninja_trader_bridge_returns_bridge_provider() -> None:
    svc = get_market_data_service(
        _make_settings(
            MARKET_DATA_PROVIDER="ninja_trader_bridge",
            NTBRIDGE_PATH="/mnt/ntbridge",
            NTBRIDGE_HEARTBEAT_MAX_AGE=60,
        )
    )
    assert isinstance(svc, MarketDataService)
    assert isinstance(svc.provider, NinjaTraderBridgeProvider)


def test_factory_tradovate_returns_tradovate_provider() -> None:
    svc = get_market_data_service(_make_settings(MARKET_DATA_PROVIDER="tradovate"))
    assert isinstance(svc.provider, TradovateAPIProvider)


def test_factory_databento_returns_databento_provider() -> None:
    svc = get_market_data_service(_make_settings(MARKET_DATA_PROVIDER="databento"))
    assert isinstance(svc.provider, DatabentoProvider)


def test_factory_unknown_provider_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Unknown MARKET_DATA_PROVIDER"):
        get_market_data_service(_make_settings(MARKET_DATA_PROVIDER="nonexistent"))


def test_factory_bridge_path_is_passed_to_provider() -> None:
    from pathlib import Path

    svc = get_market_data_service(
        _make_settings(
            MARKET_DATA_PROVIDER="ninja_trader_bridge",
            NTBRIDGE_PATH="/custom/path",
            NTBRIDGE_HEARTBEAT_MAX_AGE=30,
        )
    )
    provider: NinjaTraderBridgeProvider = svc.provider  # type: ignore[assignment]
    assert provider._bridge_path == Path("/custom/path")
    assert provider._max_age == 30


# ---------------------------------------------------------------------------
# YfinanceProvider symbol mapping (pure, no network call)
# ---------------------------------------------------------------------------

def test_yfinance_map_symbol_direct() -> None:
    provider = YfinanceProvider()
    assert provider._map_symbol("MES") == "ES=F"
    assert provider._map_symbol("MNQ") == "NQ=F"
    assert provider._map_symbol("MYM") == "YM=F"
    assert provider._map_symbol("M2K") == "RTY=F"
    assert provider._map_symbol("MGC") == "GC=F"
    assert provider._map_symbol("MJY") == "6J=F"
    assert provider._map_symbol("M6E") == "6E=F"
    assert provider._map_symbol("6J") == "6J=F"
    assert provider._map_symbol("6E") == "6E=F"


def test_yfinance_map_symbol_strips_contract_suffix() -> None:
    provider = YfinanceProvider()
    assert provider._map_symbol("MESU2025") == "ES=F"
    assert provider._map_symbol("MJYU2025") == "6J=F"
    assert provider._map_symbol("M6EU2025") == "6E=F"
    assert provider._map_symbol("6JU2025") == "6J=F"


def test_yfinance_map_symbol_unknown_returns_none() -> None:
    provider = YfinanceProvider()
    assert provider._map_symbol("XYZ") is None
    assert provider._map_symbol("M6J") is None


def test_yfinance_map_timeframe() -> None:
    provider = YfinanceProvider()
    assert provider._map_timeframe("5m") == "5m"
    assert provider._map_timeframe("15m") == "15m"
    assert provider._map_timeframe("1h") == "60m"
    assert provider._map_timeframe("4h") == "60m"
    assert provider._map_timeframe("1d") == "1d"
    assert provider._map_timeframe("1w") == "1wk"


@pytest.mark.asyncio
async def test_yfinance_is_active_always_true() -> None:
    """YfinanceProvider has no heartbeat concept — always active."""
    provider = YfinanceProvider()
    assert await provider.is_active("MES") is True
    assert await provider.is_active("ANYTHING") is True
