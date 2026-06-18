"""MarketDataService — single gateway to market data.

Architecture contract (doc 00 §9, doc 03 §8):
  - MarketDataService is the ONLY object that touches providers.
  - FilterPipeline, SLTPCalculator, QualityScorer never import providers.
  - Provider selected at startup from MARKET_DATA_PROVIDER env var.
  - In NTDEV: always yfinance. In NTEXECG prod: ninja_trader_bridge.
  - Service instance lives on app.state.market_data (set in lifespan).
  - Tests inject MockMarketDataProvider — never the real providers.

NinjaTraderBridgeProvider.is_active() checks file mtime ONLY.
Never reads file content to determine activity. Never raises on missing path.
"""
from __future__ import annotations

import asyncio
import json
import re
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import pandas_ta as ta
from loguru import logger

# ---------------------------------------------------------------------------
# ATR helper (shared by providers)
# ---------------------------------------------------------------------------

def _calc_atr(bars: list[dict], period: int = 14) -> float | None:
    """Calculate ATR from a list of OHLCV dicts using pandas-ta.

    Returns None if there are not enough bars or calculation fails.
    """
    if len(bars) < period + 1:
        return None
    try:
        df = pd.DataFrame(bars)
        df.columns = [str(c).lower() for c in df.columns]
        atr_series = ta.atr(
            df["high"].astype(float),
            df["low"].astype(float),
            df["close"].astype(float),
            length=period,
        )
        if atr_series is None or atr_series.empty:
            return None
        val = atr_series.iloc[-1]
        return float(val) if not pd.isna(val) else None
    except Exception as exc:
        logger.debug("atr_calc_failed error={}", exc)
        return None


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class MarketDataProvider(ABC):
    @abstractmethod
    async def get_bars(
        self, symbol: str, timeframe: str, limit: int = 300
    ) -> list[dict]:
        """Return OHLCV bars: [{time, open, high, low, close, volume}, ...]."""

    @abstractmethod
    async def get_atr(
        self, symbol: str, timeframe: str, period: int = 14
    ) -> float | None:
        """Return ATR value or None if not available."""

    @abstractmethod
    async def is_active(self, symbol: str) -> bool:
        """Return True if this provider has live data for the symbol."""


# ---------------------------------------------------------------------------
# YfinanceProvider — delayed ~15 min, for NTDEV only
# ---------------------------------------------------------------------------

_YF_SYMBOL_MAP: dict[str, str] = {
    "MES": "ES=F",
    "MNQ": "NQ=F",
    "MYM": "YM=F",
    "M2K": "RTY=F",
    "MGC": "GC=F",
    "MJY": "6J=F",
    "M6E": "6E=F",
    "6J": "6J=F",
    "6E": "6E=F",
}

# Futures month codes for suffix stripping (e.g., MESU2025 → MES)
_CONTRACT_SUFFIX_RE = re.compile(r"[FGHJKMNQUVXZ]\d{2,4}$")

_YF_TIMEFRAME_MAP: dict[str, str] = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "60m",
    "4h": "60m",  # yfinance has no 4h; use 60m
    "1d": "1d",
    "1w": "1wk",
}


class YfinanceProvider(MarketDataProvider):
    """Delayed yfinance data (~15 min). Development use only (NTDEV).

    Maps CME symbols to yfinance tickers:
      MES → ES=F, MNQ → NQ=F, MYM → YM=F, M2K → RTY=F, MGC → GC=F
      MJY → 6J=F, M6E → 6E=F, 6J → 6J=F, 6E → 6E=F
      MESU2025 → ES=F (strips contract month/year suffix first)
    """

    def _map_symbol(self, symbol: str) -> str | None:
        if symbol in _YF_SYMBOL_MAP:
            return _YF_SYMBOL_MAP[symbol]
        # Strip contract suffix: MESU2025 → MES, 6JU2025 → 6J
        base = _CONTRACT_SUFFIX_RE.sub("", symbol)
        return _YF_SYMBOL_MAP.get(base)

    def _map_timeframe(self, timeframe: str) -> str:
        return _YF_TIMEFRAME_MAP.get(timeframe, "5m")

    async def get_bars(
        self, symbol: str, timeframe: str, limit: int = 300
    ) -> list[dict]:
        import yfinance as yf  # lazy import — never called in tests

        yf_ticker = self._map_symbol(symbol)
        if yf_ticker is None:
            logger.warning("yfinance_no_mapping symbol={}", symbol)
            return []

        yf_interval = self._map_timeframe(timeframe)
        loop = asyncio.get_event_loop()
        try:
            df = await loop.run_in_executor(
                None,
                lambda: yf.download(
                    yf_ticker,
                    period="5d",
                    interval=yf_interval,
                    auto_adjust=True,
                    progress=False,
                    threads=False,
                ),
            )
        except Exception as exc:
            logger.warning("yfinance_download_failed symbol={} error={}", symbol, exc)
            return []

        if df is None or df.empty:
            return []

        # Flatten MultiIndex columns (newer yfinance versions return multi-level)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.columns = [str(c).lower() for c in df.columns]

        bars: list[dict] = []
        for ts, row in df.iterrows():
            try:
                bars.append({
                    "time": str(ts),
                    "open": float(row.get("open", 0) or 0),
                    "high": float(row.get("high", 0) or 0),
                    "low": float(row.get("low", 0) or 0),
                    "close": float(row.get("close", 0) or 0),
                    "volume": float(row.get("volume", 0) or 0),
                })
            except (TypeError, ValueError):
                continue

        return bars[-limit:]

    async def get_atr(
        self, symbol: str, timeframe: str, period: int = 14
    ) -> float | None:
        bars = await self.get_bars(symbol, timeframe, limit=period + 20)
        if not bars:
            return None
        return _calc_atr(bars, period)

    async def is_active(self, symbol: str) -> bool:
        # yfinance is always available (delayed data, no heartbeat concept)
        return True


# ---------------------------------------------------------------------------
# NinjaTraderBridgeProvider — real-time, production use
# ---------------------------------------------------------------------------

class NinjaTraderBridgeProvider(MarketDataProvider):
    """Reads JSON files exported by NTraderExecutionBridge.cs from NTRADER.

    Bridge path is mounted via Samba: \\NTRADER\\bridge → /mnt/ntbridge.
    NinjaTrader exports every 10 seconds to:
      bars_{symbol}_{timeframe}.json   (OHLCV bars)
      heartbeat_{symbol}.json          (every 15 seconds)

    is_active() checks heartbeat file mtime ONLY — never content.
    If bridge path is not mounted → returns False, never raises.
    """

    def __init__(self, bridge_path: str, heartbeat_max_age: int = 60) -> None:
        self._bridge_path = Path(bridge_path)
        self._max_age = heartbeat_max_age

    async def is_active(self, symbol: str) -> bool:
        """Check if NinjaTrader is actively exporting data for symbol.

        Compares heartbeat file mtime to now. Returns False if:
          - Bridge path does not exist (not mounted)
          - Heartbeat file missing
          - mtime is older than heartbeat_max_age seconds
        Never raises — bridge unavailability is a normal operational state.
        """
        heartbeat = self._bridge_path / f"heartbeat_{symbol}.json"
        try:
            if not heartbeat.exists():
                return False
            age_seconds = (
                datetime.now() - datetime.fromtimestamp(heartbeat.stat().st_mtime)
            ).total_seconds()
            return age_seconds <= self._max_age
        except OSError:
            return False

    async def get_bars(
        self, symbol: str, timeframe: str, limit: int = 300
    ) -> list[dict]:
        bars_file = self._bridge_path / f"bars_{symbol}_{timeframe}.json"
        try:
            if not bars_file.exists():
                return []
            # utf-8-sig transparently strips the UTF-8 BOM the .NET bridge writer
            # emits (and is a no-op when there is none). Without this, json.loads
            # fails with "Unexpected UTF-8 BOM".
            data = json.loads(bars_file.read_text(encoding="utf-8-sig"))
            if not isinstance(data, list):
                return []
            return data[-limit:]
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug("bridge_read_failed file={} error={}", bars_file, exc)
            return []

    async def get_atr(
        self, symbol: str, timeframe: str, period: int = 14
    ) -> float | None:
        bars = await self.get_bars(symbol, timeframe, limit=period + 10)
        if not bars:
            return None
        return _calc_atr(bars, period)


# ---------------------------------------------------------------------------
# Phase 5 stubs
# ---------------------------------------------------------------------------

class TradovateAPIProvider(MarketDataProvider):
    """Stub for Phase 5. Tradovate REST API (included free with account)."""

    async def get_bars(self, symbol: str, timeframe: str, limit: int = 300) -> list[dict]:
        raise NotImplementedError("TradovateAPIProvider not implemented until Phase 5")

    async def get_atr(self, symbol: str, timeframe: str, period: int = 14) -> float | None:
        raise NotImplementedError("TradovateAPIProvider not implemented until Phase 5")

    async def is_active(self, symbol: str) -> bool:
        return False


class DatabentoProvider(MarketDataProvider):
    """Stub for Phase 5. Databento (institutional-grade feed, $50-150/mo)."""

    async def get_bars(self, symbol: str, timeframe: str, limit: int = 300) -> list[dict]:
        raise NotImplementedError("DatabentoProvider not implemented until Phase 5")

    async def get_atr(self, symbol: str, timeframe: str, period: int = 14) -> float | None:
        raise NotImplementedError("DatabentoProvider not implemented until Phase 5")

    async def is_active(self, symbol: str) -> bool:
        return False


# ---------------------------------------------------------------------------
# MarketDataService — the only object tests / services touch
# ---------------------------------------------------------------------------

class MarketDataService:
    """Provider-agnostic market data gateway.

    Injected via app.state.market_data at startup.
    Never instantiated inside services or filters.
    """

    def __init__(self, provider: MarketDataProvider) -> None:
        self.provider = provider

    async def get_bars(
        self, symbol: str, timeframe: str, limit: int = 300
    ) -> list[dict]:
        return await self.provider.get_bars(symbol, timeframe, limit)

    async def get_atr(
        self, symbol: str, timeframe: str = "5m", period: int = 14
    ) -> float | None:
        return await self.provider.get_atr(symbol, timeframe, period)

    async def is_active(self, symbol: str) -> bool:
        return await self.provider.is_active(symbol)

    async def get_bridge_status(self, symbols: list[str] | None = None) -> dict:
        """Return activity status per symbol for dashboard display.

        Args:
            symbols: Mapped symbols to check (e.g., ["MESU2025", "MJYU2025"]).
                     Empty list → empty dict.
        Returns:
            {symbol: {active: bool, last_atr: float | None, provider: str}}
        """
        provider_name = type(self.provider).__name__
        result: dict = {}
        for symbol in (symbols or []):
            try:
                active = await self.provider.is_active(symbol)
            except Exception:
                active = False
            result[symbol] = {
                "active": active,
                "last_atr": None,  # populated by HeartbeatMonitor from MarketDataStatus
                "provider": provider_name,
            }
        return result


# ---------------------------------------------------------------------------
# Factory — called once at app startup
# ---------------------------------------------------------------------------

def get_market_data_service(settings: object) -> MarketDataService:
    """Select provider from MARKET_DATA_PROVIDER env var.

    NTDEV:       MARKET_DATA_PROVIDER=yfinance
    NTEXECG prod: MARKET_DATA_PROVIDER=ninja_trader_bridge
    """
    provider_name: str = getattr(settings, "MARKET_DATA_PROVIDER", "yfinance")

    if provider_name == "yfinance":
        return MarketDataService(YfinanceProvider())

    if provider_name == "ninja_trader_bridge":
        return MarketDataService(
            NinjaTraderBridgeProvider(
                bridge_path=getattr(settings, "NTBRIDGE_PATH", "/mnt/ntbridge"),
                heartbeat_max_age=getattr(settings, "NTBRIDGE_HEARTBEAT_MAX_AGE", 60),
            )
        )

    if provider_name == "tradovate":
        return MarketDataService(TradovateAPIProvider())

    if provider_name == "databento":
        return MarketDataService(DatabentoProvider())

    raise ValueError(
        f"Unknown MARKET_DATA_PROVIDER: {provider_name!r}. "
        "Valid values: yfinance, ninja_trader_bridge, tradovate, databento"
    )
