#!/usr/bin/env python3
"""Train HMM regime models from ohlcv_bars (manual run).

Usage (repo root, venv active, after the OHLC backfill):
    python -m scripts.train_hmm                      # all active symbols, default TF
    python -m scripts.train_hmm --symbols ES,6E
    python -m scripts.train_hmm --symbols ES --timeframe 4h

Models are written to MODELS_DIR (config). get_regime() picks them up
automatically; symbols without a model fall back to the baseline classifier.
"""
from __future__ import annotations

import argparse
import asyncio

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.services.hmm_trainer import train_active_symbols, train_symbol


async def main() -> None:
    ap = argparse.ArgumentParser(description="Train HMM regime models from ohlcv_bars")
    ap.add_argument("--symbols", default="", help="comma list (default: all active)")
    ap.add_argument("--timeframe", default="", help=f"default: {settings.HMM_REGIME_TIMEFRAME}")
    args = ap.parse_args()

    timeframe = args.timeframe or settings.HMM_REGIME_TIMEFRAME
    async with AsyncSessionLocal() as db:
        if args.symbols:
            for s in [x.strip() for x in args.symbols.split(",") if x.strip()]:
                obj = await train_symbol(db, s, timeframe)
                if obj:
                    print(f"{s:6} {timeframe}: OK  samples={obj['n_samples']}  "
                          f"states={obj['n_states']}  labels={obj['labels']}")
                else:
                    print(f"{s:6} {timeframe}: SKIP (insufficient data or fit failed)")
        else:
            results = await train_active_symbols(db, timeframe)
            for s, ok in results.items():
                print(f"{s:6} {timeframe}: {'OK' if ok else 'SKIP'}")
    print(f"\nModels dir: {settings.MODELS_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
