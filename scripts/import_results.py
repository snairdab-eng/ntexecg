#!/usr/bin/env python3
"""Import the weekly execution-results CSV → reconcile + print real metrics.

Usage (repo root, venv active):
    python -m scripts.import_results --file /path/resultados_semana.csv

CSV header (see DOCS/resultados_semanales_PLANTILLA.csv):
    signal_id,strategy_id,symbol,direction,quantity,entry_time,entry_price,
    exit_time,exit_price,pnl,exit_reason,fees

Idempotent: re-importing the same rows is a no-op.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from app.db.session import AsyncSessionLocal
from app.services.results_import import (
    compute_real_metrics,
    import_results,
    parse_rows,
)


async def main() -> None:
    ap = argparse.ArgumentParser(description="Import weekly execution results")
    ap.add_argument("--file", required=True, help="path to the weekly results CSV")
    args = ap.parse_args()

    path = Path(args.file)
    if not path.is_file():
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    rows = parse_rows(path.read_text(encoding="utf-8-sig"))
    async with AsyncSessionLocal() as db:
        summary = await import_results(db, rows)
        await db.commit()
        metrics = await compute_real_metrics(db)

    print("=== IMPORT ===")
    for k, v in summary.items():
        print(f"  {k}: {v}")

    print("\n=== MÉTRICAS REALES POR ESTRATEGIA ===")
    if not metrics:
        print("  (sin operaciones registradas)")
    for sid, m in metrics.items():
        print(f"\n[{sid}]")
        for k, v in m.items():
            print(f"  {k}: {v}")


if __name__ == "__main__":
    asyncio.run(main())
