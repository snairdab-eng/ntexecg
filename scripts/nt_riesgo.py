#!/usr/bin/env python3
"""nt_riesgo — Motor de Riesgo NTEXECG (CLI). Fase MR-1: ingesta + persistencia.

Un motor único que recibe el listado de operaciones de una estrategia LuxAlgo,
lo integra (SOBRESCRIBE el master — cada export es el histórico completo) y
persiste todo lo necesario para recrear cualquier estudio idénticamente
(CONTRATO/Motor_Riesgo_SPEC.md + Directiva del arquitecto).

REUSA el núcleo YA PROBADO del Lab (Directiva 1 — no reconstruir):
  - parser LuxAlgo, carga HOLC, validación TZ bloqueante, ATR(14) vivo y
    costura de la cola desde Postgres: `scripts/lab_analyze.py`
  - agregación (WR/PF/net/maxDD/peor — unit-agnóstica): `lab_metrics.aggregate`
  - instrumento desde el nombre del CSV: `scripts/lab_manifest.csv_instrument`

CSV-ONLY (costura jubilada): el HOLC del CSV master es la ÚNICA fuente de
historia. `ohlcv_bars`/stitch_from_db están retirados; el guardarraíl de
FRESCURA (`_guardar_frescura`) exige que el HOLC cubra la lista antes de
integrar (sin coser cola dudosa). El precio en vivo (ATR/régimen) usa el bridge.

Comandos (MR-1):
  integrar <export.csv> --codigo <codigo> [--activo SYM] [--fecha YYYY-MM-DD]
      Sobrescribe master, archiva snapshot inmutable, enriquece con ATR,
      escribe manifest reforzado y CUADRA la línea base al dólar contra el
      `PyG acumuladas USD` final del export (bloqueante si no coincide).
      FALLA si el HOLC no cubre hasta el último trade (refresca el HOLC).
  calcular <clave> [--oos 0.3] [--fecha] [--comision]
                   [--slip-pts] [--gap-pts]                       (MR-2)
      Corre los estudios de riesgo (scripts/mr_sims.py: backstop sweep,
      escalera por MAE con alta participación, TP nominal por encima del
      cierre de LuxAlgo, asimetría L/S, gating, reconciliación de fills
      con el pullback del Lab) y persiste runs/estudios_<fecha>.json.
  recrear <clave> <fecha>                                          (MR-4)
      Reproduce la corrida `fecha` desde el snapshot archivado con los
      parámetros exactos de su manifest, escribe runs/recrear_<fecha>/ y
      compara sección por sección y archivo por archivo (bit a bit).
  estado [<clave>]
      Resumen por carpeta MotorRiesgo/: nº trades, rango, cobertura HOLC,
      última integración, última corrida.

Uso (NTDEV):  .venv\\Scripts\\python.exe -m scripts.nt_riesgo integrar ...
Uso (server): .venv/bin/python -m scripts.nt_riesgo integrar ...
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import glob
import hashlib
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

# ── Núcleo del Lab (una sola fuente de verdad — Directiva 1) ──
from app.services.lab_metrics import PULLBACK_LEVELS, SL_GRID, TP_GRID
from app.services.market_data_service import _calc_atr
from scripts.lab_analyze import (
    _ATR_LOOKBACK,
    _ATR_PERIOD,
    _MIN_SANITY,
    _f,
    TRADES_DIR,
    Trade,
    detect_tz_offset,
    enrich_with_bars,
    load_holc,
    parse_luxalgo_csv,
    pullback_study,
    split_in_out,
)
from scripts.lab_manifest import csv_instrument
# ── Simuladores MR-2 (núcleo puro del motor — scripts/mr_sims.py) ──
from scripts.mr_sims import (
    ALARMA_PCT,
    CANCEL_AFTER_MAX_S,
    HaircutCfg,
    all_ladder_depths,
    from_trades,
    metrics_usd,
    proteccion_para_cuenta,
    run_studies,
)

# Override por env (igual que HOLC_DIR en el Lab): la pestaña web lanza el
# motor en subproceso y los tests lo apuntan a un dir temporal.
MOTOR_DIR = Path(os.environ.get("MOTOR_RIESGO_DIR") or "MotorRiesgo")
MANIFEST_VERSION = 1

# $/punto conocidos por instrumento (SPEC §6) — se VERIFICAN contra el export
# (`Tamaño de la posición (valor)` / precio·cant); el inferido manda si el
# instrumento no está en la tabla (6E/6J y futuros nuevos).
USD_PER_POINT_KNOWN = {"ES": 50.0, "NQ": 20.0, "RTY": 50.0, "YM": 5.0,
                       "GC": 100.0, "CL": 1000.0}

_FECHA_EN_NOMBRE = re.compile(r"_(\d{4}-\d{2}-\d{2})_")


# ---------------------------------------------------------------------------
# Lectura del pie del export: PnL acumulado final (el cuadre al dólar) y
# $/punto inferido de `Tamaño de la posición (valor)` (SPEC §6)
# ---------------------------------------------------------------------------

def read_export_footer(path: Path) -> tuple[float | None, float | None]:
    """(pnl_acumulado_final, usd_por_punto_inferido) del export crudo."""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    best_num = -1
    cum: float | None = None
    ratios: list[float] = []
    for r in rows:
        num = r.get("Trade number")
        if not num:
            continue
        n = int(num)
        c = _f(r.get("PyG acumuladas USD", ""))
        if c is not None and n > best_num:
            best_num, cum = n, c
        if (r.get("Tipo") or "").strip().lower().startswith("entrada"):
            precio = _f(r.get("Precio USD", ""))
            cant = _f(r.get("Tamaño (cant.)", ""))
            valor = _f(r.get("Tamaño de la posición (valor)", ""))
            if precio and cant and valor:
                ratios.append(valor / (precio * cant))
    ppt = round(statistics.median(ratios), 4) if ratios else None
    return cum, ppt


# ---------------------------------------------------------------------------
# ATR estimado para la cola sin HOLC (SPEC §9.2): ATR de la ÚLTIMA barra —
# marcado en enriched/manifest. CSV-only: el guardarraíl de frescura exige que
# el HOLC cubra la lista, así que esta estimación de cola casi nunca aplica.
# ---------------------------------------------------------------------------

def estimate_tail_atr(trades: list[Trade], bars: dict, offset_min: int) -> list[Trade]:
    """Asigna el ATR de la última barra HOLC a los trades POSTERIORES al
    export (sin cobertura). Devuelve los trades estimados (para marcarlos)."""
    keys = sorted(bars)
    last = keys[-1]
    window = [{"high": bars[k][1], "low": bars[k][2], "close": bars[k][3]}
              for k in keys[-(_ATR_LOOKBACK + 1):]]
    atr = _calc_atr(window, _ATR_PERIOD)
    close = bars[last][3]
    if atr is None or close <= 0:
        return []
    delta = timedelta(minutes=offset_min)
    estimated: list[Trade] = []
    for t in trades:
        if t.atr_entry is None and t.entry_ts + delta > last:
            t.atr_entry = round(atr, 6)
            t.bar_close = close
            t.atr_pct = round(atr / close * 100.0, 6)
            t.aligned_ts = t.entry_ts + delta
            t.hour = t.aligned_ts.hour
            estimated.append(t)
    return estimated


# ---------------------------------------------------------------------------
# enriched.csv (SPEC §3): por-trade +ATR14, MAE/MFE×ATR, lado, duración, sesión
# ---------------------------------------------------------------------------

def sesion_et(ts: datetime) -> str:
    """Sesión por hora ET (informativa — el filtro de sesión está descartado)."""
    hm = ts.hour * 60 + ts.minute
    if 9 * 60 + 30 <= hm < 16 * 60:
        return "RTH"
    if 16 * 60 <= hm < 18 * 60:
        return "tarde"
    if hm >= 18 * 60 or hm < 3 * 60:
        return "asia"
    return "europa"


# LOTE RIES-W — ventana de operación (sección de COBERTURA, no de filtrado).
# Orden canónico de las sesiones de sesion_et (madrugada → noche) y cuenta de
# referencia para marcar trades ROJOS (mismo default editable de la pestaña +
# ALARMA_PCT del motor). El filtro de sesión está DESCARTADO por diseño (no
# aporta edge, validado 2026-07-04): esta distribución solo dice DÓNDE caen
# los trades y las peores pérdidas, nunca recorta la señal.
_SESIONES_ORDEN = ("europa", "RTH", "tarde", "asia")
_DIAS_W = ("dom", "lun", "mar", "mié", "jue", "vie", "sáb")   # %w: 0=domingo
VENTANA_CUENTA_REF_USD = 10_000.0


def _pctl(vals: list[float], p: float) -> float | None:
    """Percentil lineal (mismo estimador que _rango_h del listado crudo)."""
    if not vals:
        return None
    s = sorted(vals)
    i = (len(s) - 1) * p
    lo, hi = int(i), min(int(i) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (i - lo), 2)


def _ventana_operacion(trades: list, offset_min: int) -> dict:
    """Sección VENTANA DE OPERACIÓN — de COBERTURA (participación 100%).

    El operador quiere la ventana recomendada según el comportamiento de los
    trades, PERO el filtro de sesión como palanca de edge está DESCARTADO por
    diseño (no aporta, validado 2026-07-04) y la filosofía es participación
    100% (capar pérdidas sin saltar señales). Por eso la recomendación por
    defecto es la ventana MÍNIMA que cubre TODAS las entradas del backtest
    (no dejar señales fuera), nunca un recorte para "mejorar" la señal.

    Reusa sesion_et y el MISMO offset ET del enriched (paridad con
    enriched.csv). Todo determinista sobre (trades, offset)."""
    delta = timedelta(minutes=offset_min)
    umbral = round(VENTANA_CUENTA_REF_USD * ALARMA_PCT / 100.0, 2)
    n_rojos = sum(1 for t in trades if t.pnl_usd <= -umbral)

    def _et(t) -> datetime:
        return t.entry_ts + delta

    # a) Distribución por sesión ET (n, net, PF, peor, % de rojos que cae ahí)
    por_sesion: dict[str, dict] = {}
    for ses in _SESIONES_ORDEN:
        sel = [t for t in trades if sesion_et(_et(t)) == ses]
        if not sel:
            continue
        m = metrics_usd([t.pnl_usd for t in sel])
        rojos = sum(1 for t in sel if t.pnl_usd <= -umbral)
        por_sesion[ses] = {
            "n": len(sel), "net_usd": m["net_usd"], "pf": m["pf"],
            "peor_trade_usd": m["peor_trade_usd"], "rojos": rojos,
            "rojos_pct": round(100 * rojos / n_rojos, 1) if n_rojos else 0.0,
        }

    # b) Rango horario observado de ENTRADAS (min–max + p05–p95) por lado/total
    def _rango(sel: list) -> dict | None:
        horas = [_et(t).hour + _et(t).minute / 60.0 for t in sel]
        if not horas:
            return None
        s = sorted(horas)
        return {"n": len(s), "min": round(s[0], 2), "max": round(s[-1], 2),
                "p05": _pctl(horas, 0.05), "p95": _pctl(horas, 0.95)}

    rango = {
        "total": _rango(trades),
        "long": _rango([t for t in trades
                        if getattr(t, "side", None) == "long"]),
        "short": _rango([t for t in trades
                         if getattr(t, "side", None) == "short"]),
    }

    # c) Ventana mínima de cobertura: días %w presentes + [floor(min),ceil(max)]
    dias_w = sorted({int(_et(t).strftime("%w")) for t in trades})
    rt = rango["total"]
    vent_min = None
    if rt:
        p95_ref = ({"hora_desde": int(math.floor(rt["p05"])),
                    "hora_hasta": int(math.ceil(rt["p95"]))}
                   if rt["p05"] is not None and rt["p95"] is not None else None)
        vent_min = {
            "dias_w": dias_w,
            "dias_label": ", ".join(_DIAS_W[d] for d in dias_w) or "—",
            "hora_desde": int(math.floor(rt["min"])),
            "hora_hasta": int(math.ceil(rt["max"])),
            "cobertura_pct": 100.0,
            "p95_ref": p95_ref,
        }

    # d) Muestras por trade [dow %w (0=dom), minuto del día ET] — para que la
    # ficha compute el % FUERA de la ventana L2 vigente sin recomputar el motor.
    muestras = [[int(_et(t).strftime("%w")), _et(t).hour * 60 + _et(t).minute]
                for t in trades]

    return {
        "nota": ("El filtro de sesión/hora NO aporta edge — DESCARTADO por "
                 "diseño (validado 2026-07-04). Esta ventana es de COBERTURA "
                 "(participación 100%): la ventana mínima que cubre TODAS las "
                 "entradas del backtest, no un recorte para mejorar la señal."),
        "cuenta_ref_usd": VENTANA_CUENTA_REF_USD,
        "umbral_rojo_usd": umbral,
        "n_trades": len(trades),
        "n_rojos": n_rojos,
        "por_sesion": por_sesion,
        "rango_horario_et": rango,
        "ventana_minima_cobertura": vent_min,
        "muestras": muestras,
    }


_ENRICHED_COLS = ("number", "side", "entry_ts", "exit_ts", "duracion_min",
                  "sesion", "entry_price", "exit_price", "pnl_usd", "pnl_pct",
                  "mae_pct", "mfe_pct", "atr_entry", "atr_pct", "mae_atr",
                  "mfe_atr", "hora_et", "atr_estimado")


def _write_holc_snapshot(path: Path, bars: dict) -> None:
    """LX-4 — escribe el HOLC (CSV master) a CSV en el formato de
    `load_holc` (DateTime,Open,High,Low,Close,Volume), para que el estudio Luxy
    lo lea por-clave y herede la cobertura de la cola (R-T2, reproducible)."""
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["DateTime", "Open", "High", "Low", "Close", "Volume"])
        for ts in sorted(bars):
            o, h, lo, c, v = bars[ts]
            w.writerow([ts.strftime("%Y-%m-%d %H:%M:%S"), o, h, lo, c, v])


def write_enriched(path: Path, trades: list[Trade], offset_min: int,
                   estimated: set[int]) -> None:
    delta = timedelta(minutes=offset_min)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(_ENRICHED_COLS)
        for t in trades:
            ts_et = t.entry_ts + delta
            dur = (round((t.exit_ts - t.entry_ts).total_seconds() / 60)
                   if t.exit_ts else None)
            w.writerow([
                t.number, t.side, t.entry_ts.isoformat(),
                t.exit_ts.isoformat() if t.exit_ts else "",
                dur if dur is not None else "", sesion_et(ts_et),
                t.entry_price, t.exit_price if t.exit_price is not None else "",
                t.pnl_usd, t.pnl_pct, t.mae_pct, t.mfe_pct,
                t.atr_entry if t.atr_entry is not None else "",
                t.atr_pct if t.atr_pct is not None else "",
                round(t.mae_atr, 4) if t.mae_atr is not None else "",
                round(t.mfe_atr, 4) if t.mfe_atr is not None else "",
                ts_et.hour, int(t.number in estimated),
            ])


# ---------------------------------------------------------------------------
# Manifest reforzado (Directiva 3.5): hash master, última barra HOLC +
# stitch, versión de rejillas, commit git del motor — `recrear` bit a bit
# ---------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def grids_fingerprint() -> str:
    """Huella de las rejillas del núcleo (lab_metrics es la fuente única)."""
    blob = json.dumps({"sl": SL_GRID, "tp": TP_GRID, "pb": PULLBACK_LEVELS})
    return "lab-" + hashlib.sha256(blob.encode()).hexdigest()[:8]


def _git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


# ---------------------------------------------------------------------------
# Carga compartida (integrar y calcular): HOLC + costura + TZ + ATR
# ---------------------------------------------------------------------------

async def _enriquecer(trades: list[Trade], activo: str,
                      ) -> tuple[dict, int, dict, int, list[Trade]]:
    """(bars, offset, tz_detail, sin_cobertura, estimados) — HOLC del CSV
    master (load_holc), validación TZ BLOQUEANTE, ATR(14) vivo y ATR estimado
    de cola. CSV-ONLY: la costura desde `ohlcv_bars` está JUBILADA — el CSV es
    la única fuente de historia y el guardarraíl de frescura (`_guardar_frescura`
    en integrar) exige que cubra la lista antes de integrar (nada de coser cola
    dudosa). Sigue async por compatibilidad del flujo (asyncio.run)."""
    bars = load_holc(activo, "5m")
    off, sanity, tz_detail = detect_tz_offset(trades, bars)
    print(f"· TZ offset {off:+d} min · sanity {sanity*100:.0f}% · "
          f"HOLC hasta {max(bars)}")
    if sanity < _MIN_SANITY:
        raise SystemExit(
            f"⛔ BLOQUEADO: sanity TZ {sanity*100:.0f}% < "
            f"{_MIN_SANITY*100:.0f}% — no se puede confiar la alineación.")
    uncovered = enrich_with_bars(trades, bars, off)
    estimated = estimate_tail_atr(trades, bars, off)
    sin_cobertura = uncovered - len(estimated)
    if estimated:
        print(f"⚠ {len(estimated)} trade(s) posteriores al HOLC → ATR "
              f"ESTIMADO (última barra {estimated[0].atr_entry:.2f}). "
              f"Refresca el HOLC para cubrir la lista (el guardarraíl lo exige).")
    if sin_cobertura:
        print(f"⚠ {sin_cobertura} trade(s) sin cobertura de barras "
              f"(inicio del HOLC): sin ATR.")
    return bars, off, tz_detail, sin_cobertura, estimated


# ---------------------------------------------------------------------------
# integrar
# ---------------------------------------------------------------------------

def _fecha_export(csv_path: Path, override: str | None) -> str:
    if override:
        return override
    m = _FECHA_EN_NOMBRE.search(csv_path.name)
    return m.group(1) if m else date.today().isoformat()


def _superset_faltantes(old_master: Path, new_trades: list[Trade]) -> list[str]:
    """Claves del master anterior AUSENTES en el export nuevo (SPEC §9.1 —
    clave estable (entry_ts, side, entry_price); advertir, no bloquear)."""
    old = parse_luxalgo_csv(old_master)
    new_keys = {(t.entry_ts.isoformat(), t.side, t.entry_price)
                for t in new_trades}
    return sorted(
        f"{t.entry_ts.isoformat()} {t.side} @{t.entry_price}"
        for t in old
        if (t.entry_ts.isoformat(), t.side, t.entry_price) not in new_keys)


# Margen del guardarraíl de frescura: el HOLC debe llegar al menos a la barra
# 5m que contiene el último trade (esa barra cubre su ventana [t, t+5m)).
_FRESCURA_MARGEN = timedelta(minutes=5)


def _guardar_frescura(trades: list[Trade], bars: dict, off: int,
                      activo: str) -> None:
    """Guardarraíl de FRESCURA (reemplaza el fail-closed de la costura jubilada):
    el CSV/HOLC master debe cubrir hasta el ÚLTIMO trade de la lista. Si el HOLC
    se quedó viejo, NO se cose una cola dudosa desde `ohlcv_bars` — se FALLA con
    un mensaje accionable pidiendo refrescar el HOLC. Offset-aware (alinea la
    hora del CSV de trades con la rejilla del HOLC, igual que enrich_with_bars)."""
    delta = timedelta(minutes=off)
    ult = max((t.exit_ts or t.entry_ts) for t in trades)
    ult_alineado = ult + delta
    holc_last = max(bars)
    if ult_alineado > holc_last + _FRESCURA_MARGEN:
        raise SystemExit(
            f"⛔ HOLC DESACTUALIZADO: el HOLC de {activo} llega hasta "
            f"{holc_last} pero la lista tiene trades hasta {ult} "
            f"(offset {off:+d} min → {ult_alineado}). Refresca el HOLC de "
            f"{activo} (re-expórtalo hasta cubrir la lista) y reintegra — "
            f"NO se cose cola dudosa.")


# ---------------------------------------------------------------------------
# LX-12 — Guardia de CONTENCIÓN al integrar (fail-honest)
# ---------------------------------------------------------------------------
# El HOLC del share puede estar en un CONTORNO DE CONTRATO distinto al del
# master (política de roll / back-adjust de NinjaTrader ≠ continuo `<sym>1!` de
# LuxAlgo): los timestamps alinean pero el NIVEL de precio está desplazado un
# escalón CONSTANTE por tramo (p.ej. 6J −91 ticks pre-junio). La sanity de
# `detect_tz_offset` NO lo ve: corrige el nivel con la mediana de los vecinos en
# tiempo (para ser robusta al roll al DETECTAR la TZ), así que un escalón
# constante pasa con sanity alta (6J: 0.96). Aquí medimos la contención CRUDA,
# SIN corrección de nivel: ¿el precio de cada entrada cae DE VERDAD dentro de
# [low,high] de su barra? Si no, el intrabar (MAE/MFE-timing, fills, BE) no
# describe estos trades → se MARCA y el estudio Luxy degrada a solo-crudo. Nunca
# se deriva una palanca de un intrabar que no contiene los precios de los trades.
CONTENCION_MIN_PCT = 80          # % mínimo de entradas dentro de su barra alineada


def _contencion(trades: list, bars: dict, off: int, activo: str) -> dict:
    """Contención intrabar del master contra su HOLC (LX-12). Devuelve:
      · pct     = % de entradas cuyo `entry_price` cae en [low,high] de su barra
                  alineada (offset aplicado) — la métrica CRUDA (sin la
                  corrección de nivel de la sanity, que absorbe el roll).
      · pct_pm1 = ídem admitiendo la barra ±1 (referencia).
      · gap_mediano_ticks_por_mes = mediana(entry_price − close) por mes en
                  TICKS (master−HOLC al mismo timestamp), si el tick del activo
                  es conocido (barato: un pase; delata el escalón de roll).
    Determinista: funciones puras sobre (trades, bars, off)."""
    delta = timedelta(minutes=off)
    keys = sorted(bars)
    index = {ts: i for i, ts in enumerate(keys)}
    try:                                        # tick full-size del activo (barato)
        from scripts.mr_report import TICK_SIZE
        tick = TICK_SIZE.get(activo)
    except Exception:
        tick = None
    n = inside = inside_pm1 = 0
    gaps_mes: dict[str, list] = {}
    for t in trades:
        ts = t.entry_ts + delta
        i = index.get(ts)
        if i is None:
            continue
        n += 1
        _o, h, lo, c, _v = bars[ts]
        if lo <= t.entry_price <= h:
            inside += 1
        for j in range(max(0, i - 1), min(len(keys), i + 2)):
            b = bars[keys[j]]
            if b[2] <= t.entry_price <= b[1]:   # b = (o,h,lo,c,v): lo=b[2], h=b[1]
                inside_pm1 += 1
                break
        if tick:
            gaps_mes.setdefault(ts.strftime("%Y-%m"), []).append(t.entry_price - c)
    pct = round(100 * inside / n, 1) if n else None
    pct_pm1 = round(100 * inside_pm1 / n, 1) if n else None
    gap_ticks = ({mes: round(statistics.median(v) / tick)
                  for mes, v in sorted(gaps_mes.items())}
                 if tick and gaps_mes else None)
    return {
        "pct": pct,
        "pct_pm1": pct_pm1,
        "n": n,
        "umbral_pct": CONTENCION_MIN_PCT,
        "confiable": bool(pct is not None and pct >= CONTENCION_MIN_PCT),
        "tick_size": tick,
        "gap_mediano_ticks_por_mes": gap_ticks,
    }


def _print_contencion(c: dict) -> None:
    """LX-12 — informe de contención + banner ACCIONABLE si no es confiable."""
    if c["confiable"]:
        print(f"· Contención intrabar {c['pct']}% "
              f"(±1 barra {c['pct_pm1']}%) ≥ {CONTENCION_MIN_PCT}% ✅")
        return
    print(f"⛔ INTRABAR NO CONFIABLE: contención {c['pct']}% "
          f"(±1 barra {c['pct_pm1']}%) < {CONTENCION_MIN_PCT}% — master y HOLC "
          f"NO comparten contorno de contrato (¿roll/back-adjust?). El master "
          f"SE INTEGRA igual, pero el estudio Luxy será DEGRADADO (solo crudo, "
          f"sin palancas intrabar). Corrige el Merge policy en NinjaTrader "
          f"(mismo continuo que LuxAlgo) y reintegra.")
    g = c.get("gap_mediano_ticks_por_mes")
    if g:
        print("  gap mediano por mes (ticks, master−HOLC): "
              + " · ".join(f"{m}:{v:+d}" for m, v in g.items()))


async def integrar(csv_path: Path, codigo: str, activo: str | None = None,
                   fecha: str | None = None,
                   degradado: bool = False) -> dict:
    if not csv_path.exists():
        raise SystemExit(f"⛔ No existe el export: {csv_path}")
    activo = activo or csv_instrument(str(csv_path))
    if not activo:
        raise SystemExit("⛔ No pude deducir el instrumento del nombre del "
                         "CSV — pásalo con --activo.")
    # Guardia de doble-prefijo: la carpeta se llavea <ACTIVO>_<codigo>; un
    # código que ya trae el prefijo crearía ES_ES_... en silencio. Abortar
    # es más seguro que normalizar sin avisar.
    if codigo.upper().startswith(f"{activo.upper()}_"):
        sugerido = codigo[len(activo) + 1:]
        raise SystemExit(
            f"⛔ El código no debe incluir el prefijo del activo "
            f"({activo}_): la carpeta sería MotorRiesgo/{activo}_{codigo}. "
            f"Usa --codigo {sugerido} → la carpeta será "
            f"{activo}_{sugerido}.")
    fecha = _fecha_export(csv_path, fecha)
    base_dir = MOTOR_DIR / f"{activo}_{codigo}"

    # 1) Parseo (núcleo del Lab) + cuadre AL DÓLAR contra el propio export
    trades = parse_luxalgo_csv(csv_path)
    if not trades:
        raise SystemExit("⛔ CSV sin trades parseables.")
    pnl_parseado = round(sum(t.pnl_usd for t in trades), 2)
    pnl_export, ppt_inferido = read_export_footer(csv_path)
    cuadre_ok = (pnl_export is not None
                 and abs(pnl_parseado - pnl_export) <= 0.01)
    if not cuadre_ok:
        raise SystemExit(
            f"⛔ CUADRE FALLIDO: Σ PnL parseado ${pnl_parseado:,.2f} ≠ "
            f"PyG acumuladas final del export "
            f"${pnl_export if pnl_export is not None else float('nan'):,.2f}. "
            f"No se integra nada.")
    print(f"· {len(trades)} trades · cuadre al dólar ✅ "
          f"(${pnl_parseado:,.2f} == export)")

    # $/punto: inferido del export, verificado contra la tabla conocida
    ppt_conocido = USD_PER_POINT_KNOWN.get(activo)
    ppt = ppt_conocido if ppt_conocido is not None else ppt_inferido
    ppt_ok = None
    if ppt_conocido is not None and ppt_inferido is not None:
        ppt_ok = abs(ppt_inferido - ppt_conocido) / ppt_conocido <= 0.01
        if not ppt_ok:
            print(f"⚠ $/punto inferido {ppt_inferido} ≠ conocido "
                  f"{ppt_conocido} — revisar contrato (¿micro vs mini?)")

    # 2) Superconjunto vs master anterior (advertir, no bloquear — SPEC §9.1)
    master = base_dir / "master.csv"
    faltantes: list[str] = []
    if master.exists():
        faltantes = _superset_faltantes(master, trades)
        if faltantes:
            print(f"⚠ El export nuevo NO es superconjunto del master anterior: "
                  f"{len(faltantes)} trade(s) del master no aparecen "
                  f"(¿export parcial? ¿ventana deslizante de LuxAlgo?):")
            for k in faltantes[:5]:
                print(f"    - {k}")

    # 3-4) HOLC + costura + TZ bloqueante + ATR (núcleo del Lab, compartido
    # con `calcular`). Modo DEGRADADO (L1: activo SIN HOLC) — opt-in explícito:
    # se SALTA la reconstrucción intrabar; el master integra igual (cuadre al
    # dólar + sha256 + snapshot). NUNCA finge números: sin ATR/enriched, el
    # manifest queda marcado `degradado` y `holc` nulo. Sin la bandera el flujo
    # es idéntico al de siempre (Riesgo v1 sin cambios).
    if degradado:
        print("⚠ Integración DEGRADADA (sin HOLC): master sin reconstrucción "
              "intrabar — sube el HOLC y reintegra para el estudio completo.")
        bars = None
        off = 0
        tz_detail = None
        sin_cobertura = len(trades)
        estimated = []
        estimated_ids = set()
        holc_last = None
        contencion = None                       # LX-12: sin HOLC no hay contención
    else:
        bars, off, tz_detail, sin_cobertura, estimated = \
            await _enriquecer(trades, activo)
        # Guardarraíl de FRESCURA (fail-closed, reemplaza la costura): el HOLC
        # debe cubrir hasta el último trade o se pide refrescarlo (nunca coser).
        _guardar_frescura(trades, bars, off, activo)
        holc_last = max(bars)
        estimated_ids = {t.number for t in estimated}
        # LX-12 — GUARDIA DE CONTENCIÓN (fail-honest): el master se integra
        # igual, pero si el HOLC no contiene los precios de los trades se MARCA
        # (intrabar_no_confiable) y el estudio degradará a solo-crudo.
        contencion = _contencion(trades, bars, off, activo)
        _print_contencion(contencion)

    # 5) Persistencia: snapshot inmutable + master + enriched + manifest
    base_dir.mkdir(parents=True, exist_ok=True)
    (base_dir / "snapshots").mkdir(exist_ok=True)
    (base_dir / "runs").mkdir(exist_ok=True)
    export_hash = _sha256(csv_path)
    snapshot = base_dir / "snapshots" / f"export_{fecha}.csv"
    if snapshot.exists() and _sha256(snapshot) != export_hash:
        snapshot = base_dir / "snapshots" / f"export_{fecha}_{export_hash[:6]}.csv"
    if not snapshot.exists():
        shutil.copyfile(csv_path, snapshot)
    shutil.copyfile(csv_path, master)
    # Sin intrabar no hay enriched (no se inventa ATR) — el estudio queda
    # pendiente de proveer el HOLC.
    if not degradado:
        write_enriched(base_dir / "enriched.csv", trades, off, estimated_ids)
        # LX-4 — snapshot del HOLC (CSV master) por-clave: el estudio Luxy
        # lo hereda por R-T2 (lee este archivo, no el HOLC global mutable), así
        # la cobertura de la cola llega a Luxy al reintegrar con costura.
        _write_holc_snapshot(base_dir / "holc_5m.csv", bars)

    base = metrics_usd([t.pnl_usd for t in trades])
    manifest = {
        "version": MANIFEST_VERSION,
        "activo": activo,
        "codigo": codigo,
        "integrado": fecha,
        "export": {
            "archivo": csv_path.name,
            "sha256_master": export_hash,
            "snapshot": snapshot.name,
        },
        "trades": {
            "n": len(trades),
            "desde": trades[0].entry_ts.isoformat(),
            "hasta": trades[-1].entry_ts.isoformat(),
            "superconjunto_ok": not faltantes,
            "faltantes_vs_anterior": len(faltantes),
        },
        "usd_por_punto": {"config": ppt_conocido, "inferido": ppt_inferido,
                          "usado": ppt, "ok": ppt_ok},
        "degradado": degradado,
        "holc": {
            "archivo": None if degradado else f"{activo}_5m.csv",
            "snapshot": None if degradado else "holc_5m.csv",   # LX-4 (por-clave)
            "ultima_barra": None if degradado else holc_last.isoformat(),
            "stitch_db": False,        # costura JUBILADA (CSV-only) — nunca cose
            "stitch": None,            # (campo conservado por compat de mr_report/recrear)
            "sin_cobertura": sin_cobertura,
            "atr_estimado": len(estimated),
            "degradado": degradado,
        },
        # LX-12 — contención intrabar (None si degradado sin HOLC) + bandera
        # fail-honest que el estudio Luxy lee para degradar a solo-crudo.
        "contencion": contencion,
        "intrabar_no_confiable": bool(
            contencion is not None and not contencion["confiable"]),
        "tz": tz_detail,
        "grids_version": grids_fingerprint(),
        "motor_commit": _git_commit(),
        "linea_base_usd": base,
        "cuadre": {"pnl_parseado": pnl_parseado, "pnl_export": pnl_export,
                   "ok": cuadre_ok},
        "ultima_corrida": None,      # la escribe `calcular` (MR-2+)
    }
    (base_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")

    print(f"\n── Línea base ({activo}_{codigo} · USD · 1 contrato) ──")
    print(f"  Total PnL      ${base['net_usd']:>12,.2f}    "
          f"Trades {base['n']}  (rentables {base['ganadores']} · "
          f"WR {base['wr_pct']}%)")
    print(f"  Profit Factor  {base['pf']:>13}    "
          f"Bruta +${base['ganancia_bruta_usd']:,.2f} / "
          f"−${base['perdida_bruta_usd']:,.2f}")
    print(f"  Max Drawdown   ${base['max_dd_usd']:>12,.2f}    "
          f"({base['max_dd_pct_hwm']}% del pico de equity)")
    print(f"  Peor trade     ${base['peor_trade_usd']:>12,.2f}")
    print(f"\n✅ Integrado → {base_dir}")
    return manifest


# ---------------------------------------------------------------------------
# calcular (MR-2): corre los estudios de riesgo y persiste el resultado
# ---------------------------------------------------------------------------

def _fmt_usd(v) -> str:
    return f"${v:,.2f}" if v is not None else "—"


def _listado_crudo(trades: list, offset_min: int = 0) -> dict:
    """Métricas del ListadoDeOperaciones COMPLETO y crudo (sin el filtro de
    universo ATR de los sims) + duración media de un trade ganador y de un
    perdedor en HORAS (pestaña v2) + R-obs-2: RANGO de tiempo de operación
    POR LADO (largos/cortos) — el dato que dimensiona el topo del
    cancel_after de TradersPost (máx duro 3600s): si el p90 del lado supera
    la hora, las piernas profundas no alcanzan a llenar.

    LOTE RIES-W: además la sección `ventana_operacion` (COBERTURA, no
    filtrado) — necesita el offset ET del enriched para calcar `hora_et`."""
    def _prom_h(sel: list) -> float | None:
        con_salida = [t for t in sel if t.exit_ts]
        if not con_salida:
            return None
        return round(sum((t.exit_ts - t.entry_ts).total_seconds()
                         for t in con_salida) / 3600.0 / len(con_salida), 1)

    def _rango_h(sel: list) -> dict | None:
        horas = sorted((t.exit_ts - t.entry_ts).total_seconds() / 3600.0
                       for t in sel if t.exit_ts)
        if not horas:
            return None

        def q(p: float) -> float:
            i = (len(horas) - 1) * p
            lo, hi = int(i), min(int(i) + 1, len(horas) - 1)
            return round(horas[lo] + (horas[hi] - horas[lo]) * (i - lo), 1)
        return {"n": len(horas), "min_h": round(horas[0], 1),
                "p50_h": q(0.5), "p90_h": q(0.9),
                "max_h": round(horas[-1], 1)}

    ganadores = [t for t in trades if t.pnl_usd > 0]
    perdedores = [t for t in trades if t.pnl_usd < 0]
    return {
        "metricas": metrics_usd([t.pnl_usd for t in trades]),
        "duracion_h": {
            "ganador_prom_h": _prom_h(ganadores),
            "perdedor_prom_h": _prom_h(perdedores),
            "n_ganadores": len(ganadores),
            "n_perdedores": len(perdedores),
        },
        # R-obs-2 — rango de operación por lado (para el topo de 1h de
        # TradersPost en las entradas límite de la escalera). getattr:
        # robusto ante trades sin `side` (fixtures viejas) → quedan fuera.
        "duracion_h_por_lado": {
            "long": _rango_h([t for t in trades
                              if getattr(t, "side", None) == "long"]),
            "short": _rango_h([t for t in trades
                               if getattr(t, "side", None) == "short"]),
        },
        # LOTE RIES-W — ventana de operación recomendada (de cobertura)
        "ventana_operacion": _ventana_operacion(trades, offset_min),
    }


# Master con más de estos días se marca como posiblemente desactualizado
# (cadencia semanal del SPEC §2 — el aviso es recordatorio, no bloqueo).
_MASTER_VIEJO_DIAS = 3


def _avisos_master(man: dict, activo: str, hoy: date,
                   exports_dir: Path, snapshots_dir: Path) -> list[str]:
    """Avisos para que calcular sobre un master desactualizado sea
    IMPOSIBLE de pasar por alto: export más nuevo sin integrar, snapshot
    más reciente que el master, o master de días atrás."""
    avisos: list[str] = []
    integrado = man["integrado"]
    for p in sorted(glob.glob(str(exports_dir / f"*_{activo}1!_*.csv"))):
        m = _FECHA_EN_NOMBRE.search(Path(p).name)
        if m and m.group(1) > integrado:
            avisos.append(
                f"hay un export más nuevo sin integrar: {Path(p).name} "
                f"({m.group(1)} > master {integrado}) — ¿integraste el "
                f"export nuevo?")
    snaps = (sorted(snapshots_dir.glob("export_*.csv"))
             if snapshots_dir.exists() else [])
    if snaps and snaps[-1].name != man["export"]["snapshot"]:
        avisos.append(
            f"el snapshot más reciente ({snaps[-1].name}) no es el del "
            f"master ({man['export']['snapshot']}) — estado inconsistente")
    try:
        dias = (hoy - date.fromisoformat(integrado)).days
    except ValueError:
        dias = None
    if dias is not None and dias > _MASTER_VIEJO_DIAS:
        avisos.append(
            f"el master de este folder es del {integrado} (hace {dias} "
            f"días) — ¿integraste el export nuevo?")
    return avisos


async def calcular(clave: str, oos: float = 0.3,
                   fecha: str | None = None,
                   hc: HaircutCfg | None = None,
                   cancel_after_s: float | None = CANCEL_AFTER_MAX_S) -> dict:
    """Corre los estudios MR-2 sobre el master de `clave` y escribe
    runs/estudios_<fecha>.json (MR-3 lo convertirá en .md + heatmap +
    recomendación). Determinista: la fecha viene del parámetro (recrear)."""
    hc = hc or HaircutCfg()
    base_dir = MOTOR_DIR / clave
    man_path = base_dir / "manifest.json"
    if not man_path.exists():
        raise SystemExit(f"⛔ No hay listado integrado en {base_dir} — "
                         f"corre `integrar` primero.")
    man = json.loads(man_path.read_text(encoding="utf-8"))
    activo = man["activo"]
    ppt = man["usd_por_punto"]["usado"]
    fecha = fecha or date.today().isoformat()

    # Identidad del master, PROMINENTE y antes de calcular nada: que el
    # desajuste "calcular sobre el export viejo" sea imposible de no ver.
    t = man["trades"]
    print("┌─ MASTER EN USO ────────────────────────────────────────────")
    print(f"│ {clave} · export {man['integrado']} · {t['n']} trades "
          f"({t['desde'][:10]} → {t['hasta'][:10]})")
    print(f"│ sha256 {man['export']['sha256_master'][:12]}… · "
          f"HOLC (al integrar) hasta {man['holc']['ultima_barra'][:16]}")
    avisos = _avisos_master(man, activo, date.today(), TRADES_DIR,
                            base_dir / "snapshots")
    for a in avisos:
        print(f"│ ⚠ {a}")
    print("└────────────────────────────────────────────────────────────")

    trades = parse_luxalgo_csv(base_dir / "master.csv")
    bars, off, tz_detail, sin_cobertura, estimated = \
        await _enriquecer(trades, activo)
    split_in_out(trades, oos)

    # Pullback del Lab (ventana 180 min) — reconciliación de fill-rates Y
    # los tiempos-al-toque (t_pb_touch) que alimentan el corte de
    # cancel_after: niveles = grilla del Lab ∪ TODAS las profundidades del
    # barrido de escalera (misma caminata B4.0, sin recolectar tiempos
    # nuevos).
    keys5 = sorted(bars)
    idx5 = {k: i for i, k in enumerate(keys5)}
    covered = [t for t in trades if t.aligned_ts in idx5]
    niveles = tuple(sorted(set(PULLBACK_LEVELS) | set(all_ladder_depths())))
    pb = pullback_study(covered, keys5, idx5, bars, levels=niveles)
    lab_rates = {lvl: d["fill_rate"] for lvl, d in pb.items()
                 if lvl in PULLBACK_LEVELS}

    sts = from_trades(trades, ppt, {t.number for t in estimated})
    res = run_studies(sts, ppt, hc, lab_rates, cancel_after_s,
                      listado_crudo=_listado_crudo(trades, off))
    res["meta"] = {
        "clave": clave, "activo": activo, "codigo": man["codigo"],
        "fecha": fecha, "oos": oos, "usd_por_punto": ppt,
        "master_sha256": man["export"]["sha256_master"],
        "holc_ultima_barra": max(bars).isoformat(),
        "stitch_db": False,        # costura jubilada (CSV-only)
        "n_trades_listado": len(trades),
        "sin_cobertura": sin_cobertura,
        "atr_estimado": len(estimated),
        "grids_version": grids_fingerprint(),
        "motor_commit": _git_commit(),
        "cancel_after_s": cancel_after_s,
        "tz": tz_detail,
    }
    out = base_dir / "runs" / f"estudios_{fecha}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(res, indent=1, ensure_ascii=False),
                   encoding="utf-8")

    # MR-3 — entregables de la corrida (md + csv + heatmap + recomendación)
    from scripts.mr_report import generar_entregables
    entregables = generar_entregables(res, base_dir / "runs")

    man["ultima_corrida"] = entregables["md"].name
    man_path.write_text(json.dumps(man, indent=1, ensure_ascii=False),
                        encoding="utf-8")

    _print_resumen_estudios(clave, res)
    print(f"\n✅ Estudios → {out}")
    for tipo, p in entregables.items():
        print(f"   {tipo:14} {p if p else '— (matplotlib no disponible)'}")
    return res


def _print_resumen_estudios(clave: str, res: dict) -> None:
    base = res["linea_base"]["total"]
    print(f"\n════ ESTUDIOS DE RIESGO — {clave} "
          f"(universo {res['universo']['n']} trades, "
          f"{res['universo']['n_atr_estimado']} con ATR estimado) ════")
    print(f"\nCRUDO (señal, sin gestión): net {_fmt_usd(base['net_usd'])} · "
          f"PF {base['pf']} · DD {_fmt_usd(base['max_dd_usd'])} · "
          f"peor {_fmt_usd(base['peor_trade_usd'])}")
    lc = res.get("listado_crudo")
    if lc:
        d = lc["duracion_h"]
        print(f"  Listado completo: {lc['metricas']['n']} trades · duración "
              f"media ganador {d['ganador_prom_h']}h "
              f"({d['n_ganadores']}) / perdedor {d['perdedor_prom_h']}h "
              f"({d['n_perdedores']})")

    g = res["mae_floor"]["ganadoras_mae_atr"]
    print(f"\n▎Suelo del SL (MAE×ATR ganadoras): mediana {g['mediana']} · "
          f"p90 {g['p90']} · p95 {g['p95']} · máx {g['max']}")
    print(f"  {res['mae_floor']['veredicto']}")

    b = res["backstop"]["optimo"]
    if b:
        print(f"\n▎Backstop óptimo: {_fmt_usd(b['backstop_usd'])} = "
              f"{b['backstop_pts']:.0f} pts ≈ {b['x_atr_mediana']}×ATR · "
              f"toca {b['tocados']} · Δnet {_fmt_usd(b['delta_net_usd'])} · "
              f"ΔDD {b['delta_dd_pct']}% · peor c/gap {b['peor_con_gap_usd']}")
    else:
        print("\n▎Backstop: NINGÚN nivel del grid suma vs base (revisar).")

    print("\n▎TP nominal (por ENCIMA del cierre de LuxAlgo — que cierre "
          "LuxAlgo):")
    for lado, d in res["tp"]["por_lado"].items():
        c = d["cierre_atr"]
        print(f"  {lado:5}: cierra p95 {c['p95']}× / p99 {c['p99']}× → "
              f"TP nominal {d['tp_nominal_atr']}×ATR "
              f"(dispararía en {d['tp_nominal_dispararia_pct']}% de los "
              f"trades) · en la mesa {_fmt_usd(d['en_la_mesa_usd'])}")
    tm = res["tp"]["tp_meta_mejor"]
    if tm:
        print(f"  TP-meta INFORMATIVO óptimo: L{tm['tp_long']}/"
              f"S{tm['tp_short']} (net {_fmt_usd(tm['net_usd'])}, "
              f"PF out {tm['pf_out']}) — no es la recomendación")

    ls = res["ls"]
    print(f"\n▎Asimetría L/S — {ls['lectura']}:")
    for lado in ("long", "short"):
        m = ls[lado]
        print(f"  {lado:5}: n {m['n']} · net {_fmt_usd(m['net_usd'])} · "
              f"PF {m['pf']} · WR {m['wr_pct']}% · "
              f"peor {_fmt_usd(m['peor_trade_usd'])} · "
              f"give-backs≥3×ATR {m['giveback_perdedores_3atr']}")

    gl = (res.get("gestion_lado") or {}).get("recomendacion")
    if gl:
        print(f"▎GESTIÓN POR LADO (estructural): {gl['accion'].upper()} "
              f"{gl['lado_malo']} — {gl['motivo']} · "
              f"solo {gl['lado_bueno']}: net "
              f"{_fmt_usd(gl['efecto_solo_lado_bueno'].get('net_usd'))} · "
              f"DD {_fmt_usd(gl['efecto_solo_lado_bueno'].get('max_dd_usd'))}"
              f" · {'⚠ muestra chica' if gl['muestra_chica'] else ''}")

    prot = res.get("proteccion")
    if prot and prot.get("combos"):
        pc = proteccion_para_cuenta(prot, 10_000.0, base)
        el = pc["elegido"]
        ef = pc["efecto"]
        print(f"\n▎PROTECCIÓN DE CUENTA (in-sample, cuenta $10,000 por "
              f"defecto — editable en la pestaña):")
        esc_p = (el.get("escalera") or {}).get("piernas") or []
        esc_txt = ("+".join(f"{p['micros']}@{p['depth_atr']:g}x"
                            for p in esc_p)
                   if (len(esc_p) > 1 or any(p["depth_atr"] > 0
                                             for p in esc_p))
                   else "única")
        print(f"  {pc['n_alarmas']} trade(s) ROJOS "
              f"(pérdida ≥{pc['umbral_alarma_pct']:.0f}% de la cuenta) · "
              f"elegido: escalera {esc_txt} · "
              f"SL {el['sl_atr'] or '—'}×ATR · backstop "
              f"{_fmt_usd(el['backstop_usd'])} · "
              f"lado {el['lado'] or 'ambos'} · "
              f"TP {'sí' if el['tp_por_lado_atr'] else '—'}")
        print(f"  efecto: peor {_fmt_usd(ef['peor_trade_usd'])} "
              f"({ef['peor_pct_cuenta']}% de la cuenta) · "
              f"DD {_fmt_usd(ef['max_dd_usd'])} · costo net "
              f"{_fmt_usd(ef['costo_net_usd'])} · part "
              f"{ef['participacion_pct']}% — {pc['etiqueta']}")

    rec = res.get("reconciliacion_fills")
    if rec:
        print(f"\n▎Reconciliación fills escalera↔pullback Lab: "
              f"Δ máx en niveles someros (≤2×ATR) = "
              f"{rec['max_delta_somero_pp']} pp")

    corte = res.get("corte_fills")
    if corte:
        print(f"\n▎Corte de fills (cancel_after {corte['cancel_after_s']:.0f}s"
              f" — los REALES de producción; sin corte = optimista):")
        print(f"  {'nivel':>6} {'sin corte':>10} {'con corte':>10} "
              f"{'retención':>10} {'t_med':>7} {'t_p90':>7}")
        for r in corte["niveles"]:
            print(f"  {r['nivel_atr']:>5}× {r['fill_sin_corte_pct']:>9}% "
                  f"{r['fill_con_corte_pct']:>9}% "
                  f"{r['retencion'] if r['retencion'] is not None else '—':>10} "
                  f"{r['t_med_min'] if r['t_med_min'] is not None else '—':>6}m "
                  f"{r['t_p90_min'] if r['t_p90_min'] is not None else '—':>6}m")
        print(f"  Tope natural de profundidad: "
              f"{corte['tope_natural_atr']}×ATR · "
              f"{corte['n_sin_datos_tiempo']} trade(s) sin datos de tiempo "
              f"(MAE aprox)")
    comp = res.get("comparativa_sin_corte")
    if comp and comp["top_net"]:
        t0 = comp["top_net"][0]
        print(f"▎Comparativa SIN corte (solo estudio): mejor net "
              f"{t0['nombre']} (${t0['net_usd']:,.0f}) · líder score "
              f"{comp['lider_score_sin_corte']}")

    print("\n▎Configs (top por net; gating = supera base + sobrevive OOS):")
    print(f"  {'config':52} {'net':>10} {'PF':>5} {'DD':>8} {'peor':>8} "
          f"{'part%':>6}  estado")
    aprobadas = [c for c in res["configs"] if c["gate"]["estado"] == "aprobada"]
    alta = [c for c in aprobadas if "alta_participacion" in c["etiquetas"]]
    top = res["configs"][:6]
    # la mejor de alta participación SIEMPRE visible (Directiva 3.1)
    if alta and alta[0] not in top:
        top.append(alta[0])
    for c in top:
        t = c["total"]
        marca = " ⚠n" if c["low_n_out"] else ""
        print(f"  {c['nombre'][:52]:52} {t['net_usd']:>10,.0f} "
              f"{t['pf'] if t['pf'] is not None else '—':>5} "
              f"{t['max_dd_usd']:>8,.0f} {t['peor_trade_usd']:>8,.0f} "
              f"{c['participacion_pct']:>6} "
              f" {c['gate']['estado']}{marca}")
    print(f"  ({len(aprobadas)} aprobadas de {len(res['configs'])} configs; "
          f"descartados por diseño: SL duro ×ATR, sesión, time-stop)")

    rob = res.get("robustez")
    if rob:
        h2h = rob.get("head_to_head")
        if h2h:
            print("\n▎Head-to-head (walk-forward):")
            for rol, t in (("líder net  ", h2h["lider_net"]),
                           ("líder score", h2h["lider_score"])):
                bl = t["bloques"]
                print(f"  {rol}: {t['nombre'][:44]:44} PF OOS "
                      f"{bl['out']['pf']} (Δ{bl['out']['delta_pf']:+}) · "
                      f"H1 {bl['h1']['pf']} / H2 {bl['h2']['pf']} → "
                      f"{t['veredicto']}")
        estres = rob.get("estres_pierna_profunda")
        if estres:
            c = estres["contribucion"]
            print(f"▎Estrés pierna profunda ({estres['micros']} MES @ "
                  f"{estres['depth_atr']}×): {estres['n_fills']} fills "
                  f"({estres['fills_por_bloque']['in']}in/"
                  f"{estres['fills_por_bloque']['out']}out · "
                  f"H1 {estres['fills_por_bloque']['h1']}/"
                  f"H2 {estres['fills_por_bloque']['h2']}) · "
                  f"aporta {c['total_usd']:+,.0f} "
                  f"({c['ganadores']}W/{c['perdedores']}L)")
        if rob["elegido"]:
            el = rob["elegido"]
            wf = el["walk_forward"]["bloques"]["out"]
            print(f"▎ELEGIDO: {el['nombre']} — PF OOS {wf['pf']} "
                  f"(Δ{wf['delta_pf']:+}) · {el['walk_forward']['veredicto']}")
        else:
            print("▎ELEGIDO: ninguno (nada validado por el walk-forward)")


# ---------------------------------------------------------------------------
# recrear (MR-4): reproducir una corrida bit a bit desde snapshot + manifest
# ---------------------------------------------------------------------------

def _comparar_secciones(orig: dict, nuevo: dict) -> list[str]:
    """Secciones del estudio que difieren (canónico, sort_keys). `meta` se
    compara SIN motor_commit: el commit registra procedencia; el candado de
    determinismo son los datos y resultados."""
    difs = []
    for k in sorted(set(orig) | set(nuevo)):
        if k == "meta":
            continue
        if (json.dumps(orig.get(k), sort_keys=True)
                != json.dumps(nuevo.get(k), sort_keys=True)):
            difs.append(k)
    mo = {x: v for x, v in orig.get("meta", {}).items()
          if x != "motor_commit"}
    mn = {x: v for x, v in nuevo.get("meta", {}).items()
          if x != "motor_commit"}
    if json.dumps(mo, sort_keys=True) != json.dumps(mn, sort_keys=True):
        difs.append("meta")
    return difs


async def recrear(clave: str, fecha: str) -> dict:
    """MR-4 — reproduce la corrida `fecha` desde el snapshot archivado con
    los parámetros EXACTOS de su manifest (SPEC §2/§9.3, Directiva 3.5:
    mismo código + mismos datos = idéntico bit a bit). Escribe la
    recreación en runs/recrear_<fecha>/ (no toca los originales) y compara
    sección por sección y archivo por archivo."""
    base_dir = MOTOR_DIR / clave
    runs = base_dir / "runs"
    orig_path = runs / f"estudios_{fecha}.json"
    if not orig_path.exists():
        raise SystemExit(f"⛔ No existe {orig_path} — nada que recrear.")
    orig = json.loads(orig_path.read_text(encoding="utf-8"))
    mo = orig["meta"]

    snap = next((p for p in sorted((base_dir / "snapshots").glob("*.csv"))
                 if _sha256(p) == mo["master_sha256"]), None)
    if snap is None:
        raise SystemExit("⛔ Ningún snapshot coincide con el sha256 del "
                         "master de la corrida — no se puede recrear.")
    print(f"🔁 recrear {clave} · corrida {fecha}")
    print(f"· snapshot {snap.name} (sha ✓ = master de la corrida)")
    print(f"· parámetros originales: oos {mo['oos']} · "
          f"stitch {'sí' if mo['stitch_db'] else 'no'} · "
          f"haircut {orig['haircut']}")

    hc = HaircutCfg(**orig["haircut"])
    trades = parse_luxalgo_csv(snap)
    bars, off, tz_detail, sin_cobertura, estimated = \
        await _enriquecer(trades, mo["activo"])
    holc_last = max(bars).isoformat()
    if holc_last != mo["holc_ultima_barra"]:
        print(f"⚠ El HOLC cambió desde la corrida original "
              f"({mo['holc_ultima_barra']} → {holc_last}): la recreación "
              f"puede diferir en la cola — deriva de DATOS, no de código.")
    split_in_out(trades, mo["oos"])
    keys5 = sorted(bars)
    idx5 = {k: i for i, k in enumerate(keys5)}
    covered = [t for t in trades if t.aligned_ts in idx5]
    niveles = tuple(sorted(set(PULLBACK_LEVELS) | set(all_ladder_depths())))
    pb = pullback_study(covered, keys5, idx5, bars, levels=niveles)
    lab_rates = {lvl: d["fill_rate"] for lvl, d in pb.items()
                 if lvl in PULLBACK_LEVELS}

    sts = from_trades(trades, mo["usd_por_punto"],
                      {t.number for t in estimated})
    # cancel_after de la corrida ORIGINAL (corridas viejas sin el campo →
    # None = modelo sin corte, fiel a lo que se corrió entonces)
    res = run_studies(sts, mo["usd_por_punto"], hc, lab_rates,
                      mo.get("cancel_after_s"),
                      listado_crudo=_listado_crudo(trades, off))
    # meta con el MISMO orden de claves que `calcular` (el original) para
    # que la serialización sea comparable byte a byte
    res["meta"] = {**mo,
                   "n_trades_listado": len(trades),
                   "sin_cobertura": sin_cobertura,
                   "atr_estimado": len(estimated),
                   "holc_ultima_barra": holc_last,
                   "grids_version": grids_fingerprint(),
                   "motor_commit": _git_commit(),
                   "cancel_after_s": mo.get("cancel_after_s"),
                   "tz": tz_detail}

    out_dir = runs / f"recrear_{fecha}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"estudios_{fecha}.json").write_text(
        json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    from scripts.mr_report import generar_entregables
    generar_entregables(res, out_dir)

    difs = _comparar_secciones(orig, res)
    commit_orig = mo.get("motor_commit")
    commit_hoy = _git_commit()

    def comparar_texto(o: Path, n: Path):
        """True | 'salvo_commit' | False | None. Los entregables registran
        el commit del motor como PROCEDENCIA: si es lo único que cambia,
        los resultados son idénticos (se reporta aparte)."""
        if not (o.exists() and n.exists()):
            return None
        to, tn = o.read_bytes(), n.read_bytes()
        if to == tn:
            return True
        if commit_orig and commit_hoy:
            so = to.decode("utf-8").replace(commit_orig, "«COMMIT»")
            sn = tn.decode("utf-8").replace(commit_hoy, "«COMMIT»")
            if so == sn:
                return "salvo_commit"
        return False

    stem = f"{clave}_{fecha}"
    archivos = {nombre: comparar_texto(runs / nombre, out_dir / nombre)
                for nombre in (f"estudios_{fecha}.json", f"Riesgo_{stem}.md",
                               f"configs_{stem}.csv",
                               f"recomendacion_{stem}.json")}
    png = f"heatmap_{stem}.png"
    png_ok = ((runs / png).read_bytes() == (out_dir / png).read_bytes()
              if (runs / png).exists() and (out_dir / png).exists()
              else None)

    identico = (not difs
                and all(v in (True, "salvo_commit")
                        for v in archivos.values() if v is not None))
    bit_a_bit = identico and all(v is True for v in archivos.values()
                                 if v is not None)
    print("\n— comparación con la corrida original —")
    n_sec = len([k for k in orig if k != "meta"]) + 1
    print(f"  secciones del estudio    "
          f"{'✓ idénticas' if not difs else '✗ difieren: ' + ', '.join(difs)}"
          f" ({n_sec - len(difs)}/{n_sec})")
    marcas = {True: "✓ bit a bit", "salvo_commit": "✓ (salvo commit)",
              False: "✗ DIFIERE", None: "— no comparado"}
    for nombre, ok in archivos.items():
        print(f"  {nombre:44} {marcas[ok]}")
    print(f"  {png:44} "
          f"{'✓ bit a bit' if png_ok else '— no comparado' if png_ok is None else '✗ difiere (informativo)'}")
    if identico and bit_a_bit:
        print(f"\n✅ RECREACIÓN IDÉNTICA BIT A BIT — determinismo "
              f"verificado (recreado en {out_dir})")
    elif identico:
        print(f"\n✅ RESULTADOS IDÉNTICOS — solo difiere el commit del "
              f"motor registrado como procedencia "
              f"({commit_orig} → {commit_hoy}); bit a bit exacto requiere "
              f"el mismo commit (Directiva 3.5: mismo código + mismos "
              f"datos). Recreado en {out_dir}")
    else:
        print(f"\n⛔ LA RECREACIÓN DIFIERE — revisar deriva de datos "
              f"(HOLC/stitch) o cambio de código (commit "
              f"{commit_orig} → {commit_hoy})")
    return {"identico": identico, "bit_a_bit": bit_a_bit,
            "difs_secciones": difs, "archivos": archivos,
            "png_identico": png_ok, "out_dir": out_dir}


# ---------------------------------------------------------------------------
# estado
# ---------------------------------------------------------------------------

def estado(clave: str | None = None) -> list[dict]:
    manifests = sorted(MOTOR_DIR.glob("*/manifest.json"))
    if clave:
        manifests = [p for p in manifests if p.parent.name == clave]
    if not manifests:
        print("(sin listados integrados — corre `integrar` primero)"
              + (f" [filtro: {clave}]" if clave else ""))
        return []
    out = []
    for p in manifests:
        m = json.loads(p.read_text(encoding="utf-8"))
        t, h = m["trades"], m["holc"]
        cobertura = ("✓" if not (h["atr_estimado"] or h["sin_cobertura"])
                     else f"⚠ {h['atr_estimado']} ATR estimado"
                          + (f", {h['sin_cobertura']} sin ATR"
                             if h["sin_cobertura"] else ""))
        ultima = m.get("ultima_corrida")
        runs = ([p.parent / "runs" / ultima] if ultima
                else sorted(p.parent.glob("runs/*")))
        print(f"▸ {p.parent.name}")
        print(f"    trades      {t['n']}  ({t['desde'][:10]} → "
              f"{t['hasta'][:10]})")
        print(f"    HOLC        hasta {h['ultima_barra'][:16]} "
              f"{'(+costura DB) ' if h['stitch_db'] else ''}· {cobertura}")
        print(f"    línea base  ${m['linea_base_usd']['net_usd']:,.2f} · "
              f"PF {m['linea_base_usd']['pf']} · "
              f"DD ${m['linea_base_usd']['max_dd_usd']:,.2f}")
        print(f"    integrado   {m['integrado']} "
              f"({m['export']['archivo']})")
        print(f"    últ. corrida {runs[-1].name if runs else '—'}")
        out.append(m)
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        prog="nt_riesgo", description="Motor de Riesgo NTEXECG (MR-1)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_int = sub.add_parser("integrar", help="integrar un export de LuxAlgo")
    p_int.add_argument("csv", type=Path)
    p_int.add_argument("--codigo", required=True,
                       help="código ESTABLE de la estrategia (llave de la "
                            "carpeta MotorRiesgo/<ACTIVO>_<codigo>)")
    p_int.add_argument("--activo", default=None,
                       help="instrumento (default: del nombre del CSV)")
    p_int.add_argument("--fecha", default=None,
                       help="fecha del export (default: del nombre del CSV)")
    p_int.add_argument("--degradado", action="store_true",
                       help="integrar SIN HOLC (sin reconstrucción intrabar): "
                            "master cuadrado al dólar, estudio pendiente de "
                            "proveer el HOLC")

    p_cal = sub.add_parser("calcular",
                           help="correr los estudios de riesgo (MR-2)")
    p_cal.add_argument("clave", help="carpeta, p.ej. ES_ConfNormal_TC_TSR")
    p_cal.add_argument("--oos", type=float, default=0.3,
                       help="fracción out-of-sample (default 0.3, como el Lab)")
    p_cal.add_argument("--fecha", default=None,
                       help="fecha de la corrida (default: hoy; `recrear` "
                            "la pasará explícita)")
    p_cal.add_argument("--comision", type=float, default=0.0,
                       help="haircut: $ por contrato round-turn (default 0 = "
                            "paridad referencia)")
    p_cal.add_argument("--slip-pts", type=float, default=0.0,
                       help="haircut: fricción en pts por pierna llenada")
    p_cal.add_argument("--gap-pts", type=float, default=0.0,
                       help="haircut: deslizamiento del backstop en pts "
                            "(el estrés 0/10/25 se reporta siempre)")
    p_cal.add_argument("--cancel-after", type=float, default=3600.0,
                       help="corte de fills de la escalera en SEGUNDOS "
                            "(default y tope 3600 = máx de TradersPost); "
                            "0 = sin corte (solo estudio)")

    p_rec = sub.add_parser(
        "recrear", help="reproducir una corrida bit a bit (MR-4)")
    p_rec.add_argument("clave", help="carpeta, p.ej. ES_ConfNormal_TC_TSR")
    p_rec.add_argument("fecha", help="fecha de la corrida a recrear "
                                     "(runs/estudios_<fecha>.json)")

    p_est = sub.add_parser("estado", help="resumen de los listados integrados")
    p_est.add_argument("clave", nargs="?", default=None,
                       help="carpeta específica, p.ej. ES_ConfNormal_TC_TSR")

    args = ap.parse_args()
    if args.cmd == "integrar":
        asyncio.run(integrar(args.csv, args.codigo, args.activo,
                             args.fecha, args.degradado))
    elif args.cmd == "calcular":
        hc = HaircutCfg(comision_rt_usd=args.comision,
                        slip_pts=args.slip_pts, gap_pts=args.gap_pts)
        ca = args.cancel_after if args.cancel_after > 0 else None
        if ca is not None and ca > CANCEL_AFTER_MAX_S:
            print(f"⚠ --cancel-after {ca:.0f}s > máximo duro de TradersPost "
                  f"({CANCEL_AFTER_MAX_S:.0f}s) — topado a 3600.")
            ca = CANCEL_AFTER_MAX_S
        asyncio.run(calcular(args.clave, args.oos,
                             args.fecha, hc, ca))
    elif args.cmd == "recrear":
        asyncio.run(recrear(args.clave, args.fecha))
    elif args.cmd == "estado":
        estado(args.clave)


if __name__ == "__main__":
    main()
