"""FIX-FX-BACKSTOP — conversión ÚNICA USD→puntos/×ATR con la rejilla del tick
del catálogo (patrón FIX-D2 `round_to_tick`). Un solo lugar que decide
REPRESENTABILIDAD; nada de `round(x,2)` disperso pensado para índices — ése era
el que colapsaba el backstop FX a 0 (6J apply 2026-07-17: $570/ppt 12.5M ≈
4.56e-5 pts → round(_,2)=0.0).

Por qué unos caminos colapsan y otros no (MATRIZ):
  · `backstop_points` está en PUNTOS DE PRECIO → su rejilla natural es el tick
    del instrumento. Para FX el tick es minúsculo (6E 5e-5, 6J 5e-7) y un
    round(_,2) fijo lo aplasta. AQUÍ vivía el espejismo.
  · `tp_nominal_*`, `levels` (C2/C3) y `c1_depth` son MULTIPLICADORES ×ATR
    ADIMENSIONALES (orden ~1..11, idénticos entre instrumentos: por eso los
    [8.0, 3.6] de 6J sobrevivieron a round(_,2)). No son precio, así que no
    tienen rejilla de tick propia — el precio final se cuantiza aguas abajo
    (`sl_tp_calculator`/`payload_builder`, FIX-D2). Su ÚNICO riesgo es colapsar
    a 0 si el operador entra un USD diminuto; se protegen con la MISMA prueba de
    representabilidad, porque `mult·ATR = usd/ppt` puntos (misma cantidad física).
"""
from __future__ import annotations

from app.services.tp_format import round_to_tick
from scripts.mr_report import FX_INSTRUMENTS, TICK_SIZE


def tick_de(activo: str | None) -> float | None:
    """Tick del catálogo para el instrumento (None si no está — fail-open)."""
    return TICK_SIZE.get(activo) if activo else None


def snap_puntos(pts, tick) -> tuple[float | None, bool, float | None]:
    """(pts_en_rejilla, representable, pts_crudos). NÚCLEO: snap de PUNTOS de
    precio al tick del catálogo (FIX-D2) + juicio de representabilidad.

    · representable = |crudo| ≥ 1 tick — por debajo, el valor cae bajo la
      resolución del instrumento y no se puede escribir sin colapsar.
    · tick None/≤0 (instrumento fuera del catálogo) → FAIL-OPEN: no snap, no
      juicio (representable=True, devuelve el crudo tal cual — nunca fabricamos
      una rejilla que no conocemos, igual que `round_to_tick`)."""
    if pts is None:
        return None, False, None
    raw = float(pts)
    if not tick or float(tick) <= 0:
        return raw, True, raw
    return round_to_tick(raw, tick), abs(raw) >= float(tick), raw


def usd_a_puntos(usd, ppt, tick) -> tuple[float | None, bool, float | None]:
    """(pts_en_rejilla, representable, pts_crudos). USD → PUNTOS de precio
    (÷ $/punto) snapped al tick. La conversión del `backstop_points`."""
    if usd is None or not ppt:
        return None, False, None
    return snap_puntos(float(usd) / float(ppt), tick)


def usd_a_mult_atr(usd, ppt, atr_med, tick) -> tuple[float | None, bool]:
    """(mult_atr, representable). USD → multiplicador ×ATR (adimensional).

    El offset de precio implícito = mult·ATR = usd/ppt puntos → MISMA prueba de
    representabilidad que el backstop (≥1 tick al ATR mediano). NO se snapa el
    multiplicador: el precio final se cuantiza al tick aguas abajo. Sin ATR o
    sin USD → (None, False)."""
    _snap, ok, raw = usd_a_puntos(usd, ppt, tick)
    if raw is None or not atr_med:
        return None, False
    return raw / float(atr_med), ok


def fmt_pts(activo: str | None, pts) -> str:
    """Display de un backstop en PUNTOS legible por instrumento (regla FX-en-
    ticks, patrón `units.fmt_atr`/`mr_report.fmt_stop`): FX en ticks del catálogo
    (nunca '0.00' ni notación científica cruda); resto en pts con :g."""
    if pts is None:
        return "—"
    p = float(pts)
    if activo in FX_INSTRUMENTS:
        tick = TICK_SIZE.get(activo)
        if tick:
            return f"{round(p / tick):,} ticks ({p:.6g} en precio)"
    return f"{p:g} pts"
