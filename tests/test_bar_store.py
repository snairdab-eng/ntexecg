"""bar_store — idempotent OHLCV persistence into ohlcv_bars (Fase 6 groundwork)."""
from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.bar_store import (
    count_bars,
    latest_bar_time,
    parse_bar_time,
    persist_bars,
)


def test_parse_bar_time_accepts_csv_bridge_and_datetime() -> None:
    assert parse_bar_time("2021-01-03 18:05:00") == datetime(2021, 1, 3, 18, 5, 0)
    assert parse_bar_time("2026-06-22T15:00:00") == datetime(2026, 6, 22, 15, 0, 0)
    assert parse_bar_time(datetime(2026, 6, 22, 15, 0, 0)) == datetime(2026, 6, 22, 15, 0)
    assert parse_bar_time("") is None
    assert parse_bar_time(None) is None
    assert parse_bar_time("not-a-date") is None


def _bars(n: int) -> list[dict]:
    """n canonical bars 5 minutes apart starting 2026-06-22 09:00 (deterministic)."""
    out = []
    for i in range(n):
        total = i * 5
        hh, mm = 9 + total // 60, total % 60
        out.append({
            "time": f"2026-06-22 {hh:02d}:{mm:02d}:00",
            "open": 100 + i, "high": 101 + i, "low": 99 + i,
            "close": 100.5 + i, "volume": 1000 + i,
        })
    return out


@pytest.mark.asyncio
async def test_persist_bars_inserts_and_is_idempotent(db: AsyncSession) -> None:
    bars = _bars(10)
    n1 = await persist_bars(db, "ES", "5m", bars)
    await db.commit()
    assert n1 == 10
    assert await count_bars(db, "ES", "5m") == 10

    # Re-persisting the same window inserts nothing (unique constraint).
    n2 = await persist_bars(db, "ES", "5m", bars)
    await db.commit()
    assert n2 == 0
    assert await count_bars(db, "ES", "5m") == 10


@pytest.mark.asyncio
async def test_persist_bars_appends_only_new(db: AsyncSession) -> None:
    await persist_bars(db, "ES", "5m", _bars(10))
    await db.commit()
    # Overlapping window: first 10 already stored, 5 are new.
    n = await persist_bars(db, "ES", "5m", _bars(15))
    await db.commit()
    assert n == 5
    assert await count_bars(db, "ES", "5m") == 15
    assert await latest_bar_time(db, "ES", "5m") == datetime(2026, 6, 22, 10, 10, 0)


@pytest.mark.asyncio
async def test_persist_bars_keyed_by_symbol_and_timeframe(db: AsyncSession) -> None:
    await persist_bars(db, "ES", "5m", _bars(5))
    await persist_bars(db, "ES", "1h", _bars(5))
    await persist_bars(db, "6E", "5m", _bars(5))
    await db.commit()
    assert await count_bars(db, "ES", "5m") == 5
    assert await count_bars(db, "ES", "1h") == 5
    assert await count_bars(db, "6E", "5m") == 5


@pytest.mark.asyncio
async def test_persist_bars_skips_bad_and_empty(db: AsyncSession) -> None:
    assert await persist_bars(db, "ES", "5m", []) == 0
    bad = [
        {"time": "bad-ts", "open": 1, "high": 2, "low": 0, "close": 1, "volume": 1},
        {"time": "2026-06-22 09:00:00", "open": "x", "high": 2, "low": 0, "close": 1},
    ]
    assert await persist_bars(db, "ES", "5m", bad) == 0
    await db.commit()
    assert await count_bars(db, "ES", "5m") == 0


@pytest.mark.asyncio
async def test_persist_bars_accepts_bridge_compact_then_normalized(db: AsyncSession) -> None:
    # The updater feeds canonical bars from market_data.get_bars (already
    # normalized from t/o/h/l/c/v). Confirm a bridge-style ISO 'T' timestamp works.
    bars = [{"time": "2026-06-22T09:05:00", "open": 5500, "high": 5505,
             "low": 5498, "close": 5502, "volume": 1200}]
    assert await persist_bars(db, "ES", "5m", bars) == 1
    await db.commit()
    assert await latest_bar_time(db, "ES", "5m") == datetime(2026, 6, 22, 9, 5, 0)
