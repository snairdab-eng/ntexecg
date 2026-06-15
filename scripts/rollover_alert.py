"""Check futures contracts expiring within N days.

Usage:
    python scripts/rollover_alert.py --days 7

Reads the DB connection from .env. Prints a table of active SymbolMap rows
with an expiry_date, highlighting contracts expiring within the threshold.
Exit code 1 if any contract is within the warning window (useful for cron).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# ANSI colors (cron logs ignore them harmlessly)
_RED = "\033[91m"
_AMBER = "\033[93m"
_GREEN = "\033[92m"
_RESET = "\033[0m"


async def _fetch(threshold_days: int) -> list[dict]:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import settings
    from app.models.symbol_map import SymbolMap

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    today = date.today()
    try:
        async with factory() as db:
            result = await db.execute(
                select(SymbolMap)
                .where(SymbolMap.active.is_(True), SymbolMap.expiry_date.is_not(None))
                .order_by(SymbolMap.expiry_date)
            )
            rows = []
            for sm in result.scalars().all():
                days_remaining = (sm.expiry_date - today).days
                rows.append({
                    "tv_symbol": sm.tv_symbol,
                    "mapped_symbol": sm.mapped_symbol,
                    "expiry_date": sm.expiry_date,
                    "days_remaining": days_remaining,
                })
            return rows
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Contract rollover alert.")
    parser.add_argument("--days", type=int, default=7, help="Warning threshold in days")
    args = parser.parse_args()

    rows = asyncio.run(_fetch(args.days))

    if not rows:
        print("No hay contratos activos con fecha de expiración registrada.")
        return 0

    print(f"Contratos activos (umbral de alerta: {args.days} días)\n")
    print(f"{'TV':<6} {'Contrato':<12} {'Expira':<12} {'Días':>5}")
    print("-" * 38)

    warnings = 0
    for r in rows:
        d = r["days_remaining"]
        if d <= args.days:
            color = _RED if d <= 7 else _AMBER
            warnings += 1
        else:
            color = _GREEN
        line = (
            f"{r['tv_symbol']:<6} {r['mapped_symbol']:<12} "
            f"{r['expiry_date'].strftime('%Y-%m-%d'):<12} {d:>5}"
        )
        suffix = "  ⚠ ROLLOVER" if d <= args.days else ""
        print(f"{color}{line}{suffix}{_RESET}")

    print()
    if warnings:
        print(f"{_RED}⚠ {warnings} contrato(s) requieren rollover dentro de "
              f"{args.days} días. Actualizar Symbol Mapper.{_RESET}")
        return 1
    print(f"{_GREEN}✓ Ningún contrato expira dentro de {args.days} días.{_RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
