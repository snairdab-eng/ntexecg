"""Formateo de unidades para la capa web — criterio 'FX en TICKS' (regla
P1-2 / R-obs-4), FUENTE ÚNICA del Symbol Mapper (tick_size).

Un ATR de precio sub-céntimo (yen ~0.00004, euro ~0.00005) redondeado a 2
decimales es un '0.00' engañoso. Con el tick_size del catálogo se expresa en
TICKS del instrumento. Mismo patrón que routes_riesgo._fmt_unidad y
scripts.mr_report.fmt_stop (allí para stops en $; aquí para los ATR del panel
del bridge)."""
from __future__ import annotations


def fmt_atr(atr, tick_size) -> str:
    """ATR para la tabla del bridge.

    - Sin ATR → '—'.
    - Con tick_size y ATR (o el propio tick) sub-céntimo (FX) → en TICKS:
      '72 ticks (3.6e-05)'. NUNCA '0.00'.
    - Resto (índices, sin tick_size en catálogo) → 2 decimales.
    """
    if atr is None:
        return "—"
    atr = float(atr)
    ts = float(tick_size) if tick_size else None
    if ts and (atr < 0.01 or ts < 0.01):
        return f"{round(atr / ts):,} ticks ({atr:g})"
    return f"{atr:.2f}"
