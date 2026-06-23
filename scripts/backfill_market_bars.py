#!/usr/bin/env python3
"""Backfill ohlcv_bars from the NinjaTrader HOLC CSV exports (one-time, idempotent).

Each file is named <SYMBOL>_<TF>.csv (e.g. ES_5m.csv, 6E_1h.csv) with header
    DateTime,Open,High,Low,Close,Volume
and a UTF-8 BOM. Loads them into the ohlcv_bars table with provider="ninjatrader"
so the live-feed updater (MarketBarsUpdater) deduplicates against this history.

Usage (run from the repo root, venv active):
    python -m scripts.backfill_market_bars --dir /path/to/HOLC
    python -m scripts.backfill_market_bars --dir ./HOLC --symbols ES,6E
    python -m scripts.backfill_market_bars --dir ./HOLC --timeframes 1h,4h

Safe to re-run: existing bars are skipped via the unique constraint.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from pathlib import Path

from app.db.session import AsyncSessionLocal
from app.services.bar_store import PROVIDER, persist_bars

_VALID_TF = {"5m", "15m", "1h", "4h"}
_CHUNK = 5000


def _parse_name(path: Path) -> tuple[str | None, str | None]:
    stem = path.stem
    if "_" not in stem:
        return None, None
    symbol, timeframe = stem.rsplit("_", 1)
    return symbol, timeframe


async def _load_file(path: Path, symbol: str, timeframe: str) -> tuple[int, int]:
    total_rows = total_new = 0
    async with AsyncSessionLocal() as db:
        chunk: list[dict] = []
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                chunk.append({
                    "time": row.get("DateTime"),
                    "open": row.get("Open"), "high": row.get("High"),
                    "low": row.get("Low"), "close": row.get("Close"),
                    "volume": row.get("Volume"),
                })
                total_rows += 1
                if len(chunk) >= _CHUNK:
                    total_new += await persist_bars(db, symbol, timeframe, chunk)
                    await db.commit()
                    chunk = []
            if chunk:
                total_new += await persist_bars(db, symbol, timeframe, chunk)
                await db.commit()
    return total_rows, total_new


async def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill ohlcv_bars from HOLC CSVs")
    ap.add_argument("--dir", required=True, help="directory with <SYMBOL>_<TF>.csv files")
    ap.add_argument("--symbols", default="", help="comma list to restrict (e.g. ES,6E)")
    ap.add_argument("--timeframes", default="", help="comma list to restrict (e.g. 1h,4h)")
    args = ap.parse_args()

    data_dir = Path(args.dir)
    if not data_dir.is_dir():
        print(f"ERROR: directory not found: {data_dir}", file=sys.stderr)
        sys.exit(1)

    only_sym = {s.strip() for s in args.symbols.split(",") if s.strip()}
    only_tf = {s.strip() for s in args.timeframes.split(",") if s.strip()}

    grand_rows = grand_new = 0
    for path in sorted(data_dir.glob("*.csv")):
        symbol, timeframe = _parse_name(path)
        if not symbol or timeframe not in _VALID_TF:
            print(f"skip (name) {path.name}")
            continue
        if only_sym and symbol not in only_sym:
            continue
        if only_tf and timeframe not in only_tf:
            continue
        rows, new = await _load_file(path, symbol, timeframe)
        grand_rows += rows
        grand_new += new
        print(f"{path.name:16} rows={rows:>9}  new={new:>9}")

    print(f"\nTOTAL rows={grand_rows}  new={grand_new}  provider={PROVIDER}")


if __name__ == "__main__":
    asyncio.run(main())
