#!/usr/bin/env python3
"""audit_ohlcv_tz — LX-6, criterio 0: determina EMPÍRICAMENTE la convención
horaria de las filas de `ohlcv_bars` por PROVENIENCIA (backfill vs updater) y
RANGO, comparándolas contra el HOLC CSV (ET-naive canónico). Reporta si el
mislabel es homogéneo o heterogéneo, ANTES de corregir nada.

Idea: en el rango de SOLAPE (mismos timestamps que el CSV) una fila bien
etiquetada (ET) coincide en cierre; una fila corrida por TZ NO coincide en su
timestamp pero SÍ coincide si se la desplaza por el offset ET↔UTC de esa fecha.
El script prueba desplazamientos candidatos y cuenta cuántas filas "encajan" con
cada uno — así se ve la convención real sin normalizar a ciegas.

Uso (server):
  python -m scripts.audit_ohlcv_tz --symbol ES --tf 5m           # auditar (read-only)
  python -m scripts.audit_ohlcv_tz --symbol ES --tf 5m --fix     # DRY-RUN del fix
  python -m scripts.audit_ohlcv_tz --symbol ES --tf 5m --fix --apply   # aplicar (gated)

NO normaliza a ciegas: sólo con --fix --apply escribe, y sólo las filas cuyo
desplazamiento las hace COINCIDIR con el CSV en el solape (auto-verificable).
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import timedelta

# Offsets candidatos ET↔UTC (EST −5h, EDT −4h). Una fila UTC-naive guardada como
# si fuera ET aparece +4/+5h adelantada respecto al CSV ET.
_CANDIDATOS_MIN = (0, -240, -300, 240, 300)


async def audit(symbol: str, tf: str, do_fix: bool, apply: bool) -> None:
    from sqlalchemy import select
    from app.db.session import AsyncSessionLocal
    from app.models.ohlcv_bar import OhlcvBar
    from scripts.lab_analyze import load_holc, _et_naive

    csv = load_holc(symbol, tf)                     # ET-naive canónico
    csv_close = {ts: v[3] for ts, v in csv.items()}
    csv_min, csv_max = min(csv), max(csv)
    tol = lambda a, b: abs(a - b) <= max(0.001 * abs(b), 1e-9)

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(OhlcvBar).where(OhlcvBar.symbol == symbol,
                                   OhlcvBar.timeframe == tf)
            .order_by(OhlcvBar.bar_time))).scalars().all()
    print(f"ohlcv_bars {symbol} {tf}: {len(rows)} filas · CSV ET {csv_min} → {csv_max}")
    if not rows:
        return

    aware = sum(1 for r in rows if r.bar_time.tzinfo is not None)
    print(f"  tz-aware (columna timestamptz): {aware}/{len(rows)} "
          f"→ la lectura las normaliza a ET con astimezone(NY)")

    # ¿qué desplazamiento hace que las filas del SOLAPE encajen con el CSV?
    encaje = Counter()
    fuera = 0
    por_provider = Counter()
    for r in rows:
        ts = _et_naive(r.bar_time)
        por_provider[r.provider] += 1
        if not (csv_min <= ts <= csv_max):
            fuera += 1
            continue
        matched = None
        for off in _CANDIDATOS_MIN:
            k = ts + timedelta(minutes=off)
            if k in csv_close and tol(float(r.close), csv_close[k]):
                matched = off
                break
        encaje[matched] += 1

    print("  === encaje en el SOLAPE (desplazamiento que alinea con el CSV) ===")
    for off, n in sorted(encaje.items(), key=lambda kv: -kv[1]):
        etq = ("ET (coincide directo)" if off == 0
               else "sin encaje (¿ruido/símbolo?)" if off is None
               else f"corrido {off:+d} min (mislabel TZ)")
        print(f"    {etq}: {n}")
    print(f"    fuera del rango del CSV (cola/pre-inicio, no verificable): {fuera}")
    print(f"  proveniencia: {dict(por_provider)}")

    homog = (len([o for o in encaje if o not in (0, None)]) == 0)
    print("  VEREDICTO:",
          "homogéneo ET (sano)" if homog and encaje.get(0, 0)
          else "HETEROGÉNEO — hay filas corridas por TZ (revisar escritor)")

    if not do_fix:
        return
    # DRY-RUN / apply: sólo corrige filas cuyo desplazamiento las hace coincidir.
    corregibles = [(r, off) for r, off in (
        (r, next((o for o in _CANDIDATOS_MIN if o not in (0,)
                  and (_et_naive(r.bar_time) + timedelta(minutes=o)) in csv_close
                  and tol(float(r.close), csv_close[_et_naive(r.bar_time) + timedelta(minutes=o)])), None))
        for r in rows) if off is not None]
    print(f"  === FIX ({'APPLY' if apply else 'DRY-RUN'}) === corregibles (auto-verificables): {len(corregibles)}")
    if apply and corregibles:
        for r, off in corregibles:
            r.bar_time = _et_naive(r.bar_time) + timedelta(minutes=off)
        async with AsyncSessionLocal() as db:
            for r, _ in corregibles:
                await db.merge(r)
            await db.commit()
        print("  aplicado.")
    elif not apply:
        print("  (dry-run: no se escribió nada; añade --apply para corregir. Las "
              "filas de la COLA fuera del CSV no son auto-verificables y no se tocan.)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--tf", default="5m")
    ap.add_argument("--fix", action="store_true", help="evaluar corrección (dry-run)")
    ap.add_argument("--apply", action="store_true", help="ESCRIBIR (requiere --fix)")
    a = ap.parse_args()
    asyncio.run(audit(a.symbol, a.tf, a.fix, a.apply and a.fix))


if __name__ == "__main__":
    main()
