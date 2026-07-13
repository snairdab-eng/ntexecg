#!/usr/bin/env python3
"""ohlcv_inventory — GATE del rebuild de ohlcv_bars (LX-6). SOLO LECTURA.

Compara, por (símbolo, timeframe), el RANGO cubierto por los HOLC CSV
(fuente del backfill histórico) contra el RANGO actualmente en `ohlcv_bars`.
Sirve para decidir si un TRUNCATE + re-backfill reconstruye el 100% del rango:

  · pre_gap  — ¿el CSV empieza en o antes del primer bar de la DB? Si el CSV
               empieza DESPUÉS, el TRUNCATE perdería historia vieja (los CSV no
               la cubren).
  · tail_gap — DB_last − CSV_last. Es la COLA que el CSV NO cubre; solo el
               bridge (o el updater) puede reponerla. Si es grande y el bridge
               solo guarda ~500 barras, el rebuild perdería ese tramo.

VERDICTO por fila: OK-para-truncate solo si pre_gap está cubierto Y tail_gap es
0 o cae dentro de lo que el bridge puede reponer (verificar aparte el span de
NINJATRADER/bridge/bars_<sym>_<tf>.json). Este script NO escribe nada.

Uso (server):  .venv/bin/python -m scripts.ohlcv_inventory
               .venv/bin/python -m scripts.ohlcv_inventory --symbols ES,NQ
"""
from __future__ import annotations

import argparse
import asyncio
import csv as _csv
import glob
from datetime import datetime
from pathlib import Path

from scripts.lab_analyze import _holc_dir

_VALID_TF = ("5m", "15m", "1h", "4h")


def _csv_range(path: Path) -> tuple[datetime | None, datetime | None, int]:
    """(primero, último, n) leyendo solo la columna DateTime del HOLC CSV."""
    first = last = None
    n = 0
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for r in _csv.DictReader(fh):
            raw = (r.get("DateTime") or "").strip()
            if not raw:
                continue
            try:
                ts = datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            if first is None:
                first = ts
            last = ts
            n += 1
    return first, last, n


async def _db_ranges(symbols: set[str]) -> dict[tuple[str, str], tuple]:
    """{(symbol, tf): (min, max, count)} de ohlcv_bars. {} si la DB no responde."""
    from sqlalchemy import func, select
    from app.db.session import AsyncSessionLocal
    from app.models.ohlcv_bar import OhlcvBar

    out: dict[tuple[str, str], tuple] = {}
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(OhlcvBar.symbol, OhlcvBar.timeframe,
                   func.min(OhlcvBar.bar_time), func.max(OhlcvBar.bar_time),
                   func.count())
            .group_by(OhlcvBar.symbol, OhlcvBar.timeframe))).all()
    for sym, tf, lo, hi, n in rows:
        if symbols and sym not in symbols:
            continue
        out[(sym, tf)] = (lo, hi, n)
    return out


def _fmt(ts) -> str:
    return ts.strftime("%Y-%m-%d %H:%M") if ts else "—"


async def main() -> None:
    ap = argparse.ArgumentParser(description="Inventario CSV vs ohlcv_bars (gate rebuild)")
    ap.add_argument("--symbols", default="", help="lista separada por comas (ES,NQ)")
    args = ap.parse_args()
    only = {s.strip() for s in args.symbols.split(",") if s.strip()}

    holc = _holc_dir()
    csv_ranges: dict[tuple[str, str], tuple] = {}
    for p in sorted(glob.glob(str(holc / "*.csv"))):
        stem = Path(p).stem
        if "_" not in stem:
            continue
        sym, tf = stem.rsplit("_", 1)
        if tf not in _VALID_TF or (only and sym not in only):
            continue
        csv_ranges[(sym, tf)] = _csv_range(Path(p))

    try:
        db_ranges = await _db_ranges(only)
        db_err = None
    except Exception as exc:                        # DB no accesible → solo CSV
        db_ranges, db_err = {}, exc

    print(f"HOLC_DIR: {holc}")
    if db_err:
        print(f"⚠ ohlcv_bars NO accesible ({type(db_err).__name__}: {db_err}) — "
              f"solo inventario CSV.\n")

    keys = sorted(set(csv_ranges) | set(db_ranges))
    hdr = (f"{'sym':6} {'tf':4} | {'CSV first':16} {'CSV last':16} {'n_csv':>8} "
           f"| {'DB first':16} {'DB last':16} {'n_db':>9} | veredicto")
    print(hdr)
    print("-" * len(hdr))
    for sym, tf in keys:
        cf, cl, cn = csv_ranges.get((sym, tf), (None, None, 0))
        dlo, dhi, dn = db_ranges.get((sym, tf), (None, None, 0))
        # _et_naive por si la DB aún devuelve tz-aware (pre-migración)
        if dlo is not None or dhi is not None:
            from scripts.lab_analyze import _et_naive
            dlo = _et_naive(dlo) if dlo else None
            dhi = _et_naive(dhi) if dhi else None
        verdicto = ""
        if not db_ranges:
            verdicto = "(sin DB)"
        elif (sym, tf) not in db_ranges:
            verdicto = "solo CSV (no en DB)"
        elif (sym, tf) not in csv_ranges:
            verdicto = "⛔ en DB pero SIN CSV — rebuild NO lo cubre"
        else:
            pre_ok = cf is not None and dlo is not None and cf <= dlo
            tail = (dhi - cl) if (dhi and cl) else None
            partes = []
            partes.append("pre✓" if pre_ok else "⛔pre (CSV empieza tarde)")
            if tail is None:
                partes.append("tail?")
            elif tail.total_seconds() <= 0:
                partes.append("tail✓ (CSV llega al final)")
            else:
                partes.append(f"⚠tail {tail} → solo bridge")
            verdicto = " · ".join(partes)
        print(f"{sym:6} {tf:4} | {_fmt(cf):16} {_fmt(cl):16} {cn:>8} "
              f"| {_fmt(dlo):16} {_fmt(dhi):16} {dn:>9} | {verdicto}")

    print("\nGate TRUNCATE: OK solo si TODAS las filas con datos en DB muestran "
          "pre✓ y (tail✓ o el tail cae dentro del span del bridge JSON).")


if __name__ == "__main__":
    asyncio.run(main())
