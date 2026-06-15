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


def clear_cache() -> None:
    """Clear the symbol mapper cache. Call this in tests between fixtures."""
    _cache.clear()


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
