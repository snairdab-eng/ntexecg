"""SymbolMapper — direct DB lookup only. No string manipulation whatsoever.

The operator configures the ticker manually in LuxAlgo.
NTEXECG never transforms, infers, or guesses the ticker.

"MJY" → "MJYU2025"  ✅  (direct lookup)
"M6J" → None         ✅  (does not exist in CME — Micro Yen is MJY, not M6J)
"MES1!" → None       ✅  (wrong format, not in table)
"mes" → None         ✅  (case-sensitive, no .upper())
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.symbol_map import SymbolMap
from app.services.repositories import get_active_symbol_map

_CACHE_TTL = timedelta(minutes=5)

# Module-level cache: tv_symbol → (mapped_symbol | None, cached_at)
_cache: dict[str, tuple[str | None, datetime]] = {}

# Separate cache for the market-data alias: ticker_received → (data_symbol, cached_at)
_data_symbol_cache: dict[str, tuple[str, datetime]] = {}


def clear_cache() -> None:
    """Clear the symbol mapper caches. Call this in tests between fixtures."""
    _cache.clear()
    _data_symbol_cache.clear()


class SymbolMapper:
    """Direct lookup: tv_symbol → mapped_symbol.

    Cache is module-level with 5-minute TTL so it persists across requests
    in production. Call clear_cache() in tests to reset between test cases.
    """

    async def map_symbol(self, db: AsyncSession, ticker_received: str) -> str | None:
        """Return mapped_symbol for ticker_received, or None if not in table.

        CRITICAL: ticker_received is used as-is for the lookup.
        No .upper(), no .strip(), no prefix logic, no string manipulation.
        """
        now = datetime.now(timezone.utc)
        if ticker_received in _cache:
            cached_value, cached_at = _cache[ticker_received]
            if now - cached_at < _CACHE_TTL:
                return cached_value

        symbol_map = await get_active_symbol_map(db, ticker_received)
        result = symbol_map.mapped_symbol if symbol_map else None
        _cache[ticker_received] = (result, now)
        return result

    async def resolve_market_data_symbol(
        self, db: AsyncSession, ticker_received: str
    ) -> str:
        """Return the bridge symbol whose market data should be read for this ticker.

        Read-only symbol substitution (Anexo A.9.1; reglas 36, 38): a micro reads
        the bridge files of its more-liquid parent. Looks up the active SymbolMap
        by exact tv_symbol; returns its market_data_symbol if set/non-empty,
        otherwise the ticker_received itself (parents, and unknown tickers, map to
        themselves). NEVER returns None — the caller always gets a symbol to read.

        Same exact-match, no-string-manipulation contract as map_symbol().
        Used ONLY for choosing which bridge files to read (is_active / get_atr).
        Decisions and the TradersPost payload keep using the mapped contract.
        """
        now = datetime.now(timezone.utc)
        if ticker_received in _data_symbol_cache:
            cached_value, cached_at = _data_symbol_cache[ticker_received]
            if now - cached_at < _CACHE_TTL:
                return cached_value

        symbol_map = await get_active_symbol_map(db, ticker_received)
        alias = symbol_map.market_data_symbol if symbol_map else None
        # NULL or empty string → fall back to the ticker itself.
        result = alias if alias else ticker_received
        _data_symbol_cache[ticker_received] = (result, now)
        return result

    async def get_pine_script_config(
        self, db: AsyncSession, tv_symbol: str
    ) -> str | None:
        """Return the pine_script_config string for UI display.
        e.g. '"ticker": "MJY"'
        """
        symbol_map = await get_active_symbol_map(db, tv_symbol)
        return symbol_map.pine_script_config if symbol_map else None

    async def check_expiring_contracts(
        self, db: AsyncSession, alert_days: int = 7
    ) -> list[SymbolMap]:
        """Return active SymbolMap rows where expiry_date <= today + alert_days."""
        cutoff = date.today() + timedelta(days=alert_days)
        result = await db.execute(
            select(SymbolMap).where(
                SymbolMap.active.is_(True),
                SymbolMap.expiry_date <= cutoff,
            )
        )
        return list(result.scalars().all())
