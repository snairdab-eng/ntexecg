#!/usr/bin/env python3
"""lab_analyze — Laboratorio (camino A): analítica offline por estrategia.

Fase 1: parser del CSV de LuxAlgo (ListaDeOperaciones) + OHLC histórico
(NINJATRADER/HOLC, con costura opcional del OhlcvBar de Postgres para la cola
reciente) → línea base, SL sweep re-simulado y edge por hora, con partición
in/out-of-sample y validación BLOQUEANTE de zona horaria.

READ-ONLY respecto al sistema vivo: no toca dispatch, TradersPost ni posiciones;
la DB solo se LEE y solo con --stitch-db. Reusa la lógica viva (ATR de
market_data_service; la fórmula de SL es la misma semántica k·ATR de
sl_tp_calculator, re-expresada en % para el re-sim del Anexo 25 §8.1).

Uso:
  python -m scripts.lab_analyze --instrument ES [--csv <ruta>] [--oos 0.3]
                                [--stitch-db] [--sample 60]
Escribe REPORTES/LAB_<instrumento>_<fecha>.md y cachea la matriz de features
en REPORTES/lab_features_<instrumento>.json.

Env:
  HOLC_DIR  directorio de los CSV HOLC ({SYM}_{tf}.csv). Default:
            NINJATRADER/HOLC relativo al cwd (NTDEV); en el server apuntar
            a la ruta absoluta de los datos (/home/cadmin/holc_data), igual
            que la app parametriza NTBRIDGE_PATH/DATABASE_URL por env.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import glob
import json
import os
import statistics
import sys

# Consola Windows (cp1252) vs unicode de los prints — el .md siempre va UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from bisect import bisect_right
from types import SimpleNamespace
from datetime import timezone as _utc_tz
from zoneinfo import ZoneInfo

from app.services.hmm_service import classify_regime      # lógica viva (Kaufman ER)
# Núcleo de agregación COMPARTIDO con el visor (camino B): una sola fuente de
# verdad — el reporte offline y el endpoint del UI llaman a estas funciones.
from app.services.lab_metrics import (
    EMA_KEYS,
    PULLBACK_LEVELS,
    REGIME_GATE_DEFS,
    SL_GRID,
    SUB_NAMES,
    SUB_THRESHOLDS,
    TP_GRID,
    aggregate,
    baseline_from_rows,
    hourly_from_rows,
    lift_from_rows,
    resim_rows,
    survivors_from_lifts,
)
from app.services.market_data_service import _calc_atr    # lógica viva de ATR
from app.services.quality_scorer import _SUBSCORES        # lógica viva (4 subscores)
# Fase 3 — MISMO estimador de cancel_after que el estudio vivo (reconciliados:
# min(3600, int(p90_min*60)+60)); no se inventa un segundo p90.
from scripts.pullback_timing import pctl, suggest_cancel_after

_NY = ZoneInfo("America/New_York")

def _holc_dir() -> Path:
    """Directorio de los export HOLC — override por env HOLC_DIR (en el server
    los datos viven fuera del repo: /home/cadmin/holc_data); sin env, la ruta
    relativa histórica (NTDEV, depende del cwd = raíz del repo)."""
    return Path(os.environ.get("HOLC_DIR") or "NINJATRADER/HOLC")


TRADES_DIR = Path("ListaDeOperaciones")
REPORTES = Path("REPORTES")

SL_KS = (1.5, 2.0, 2.5, 3.0, 4.0, 6.0, 8.0)
# Offsets candidatos (minutos): horas enteras UTC/ET/CT/… y variante +5m por si
# el HOLC estampa la barra por su CIERRE y el CSV por su apertura.
_CANDIDATE_OFFSETS = [h * 60 + m for h in range(-8, 9) for m in (0, 5)]
_MIN_SANITY = 0.70          # bloqueante: % de precios dentro de su barra
_ATR_PERIOD = 14
_ATR_LOOKBACK = 40          # barras hacia atrás para calcular ATR(14)
_LOW_N = 10                 # marca de "n bajo" en buckets


# ---------------------------------------------------------------------------
# Parser del CSV de LuxAlgo (2 filas por trade, pareadas por Trade number)
# ---------------------------------------------------------------------------

@dataclass
class Trade:
    number: int
    side: str                 # "long" | "short"
    entry_ts: datetime        # TZ del chart (se alinea después con el offset)
    exit_ts: datetime | None
    entry_price: float
    exit_price: float | None
    pnl_usd: float
    pnl_pct: float
    mfe_pct: float            # Desviación favorable % (>= 0)
    mae_pct: float            # |Desviación adversa %| (>= 0)
    # Enriquecido con el OHLC (None si la barra no está cubierta):
    atr_entry: float | None = None
    atr_pct: float | None = None
    bar_close: float | None = None
    hour: int | None = None   # hora en la TZ del OHLC (ET)
    in_sample: bool = True
    aligned_ts: datetime | None = None   # entry_ts + offset (clave del OHLC)
    # LX-13 — contención POR TRADE: True si el precio de entrada NO cae en
    # [low,high] de su barra alineada ni de las ±vecinas (outlier de frontera de
    # roll); su intrabar individual es basura → se excluye del universo simulable
    # como los ATR-estimados (nunca toca el crudo ni el cuadre).
    no_contenido: bool = False
    gap_ticks: float | None = None       # diagnóstico: (precio − close) en ticks
    # Fase 2 — features (None si sin cobertura):
    sub_volume: float | None = None
    sub_atr: float | None = None
    sub_vwap: float | None = None
    sub_time: float | None = None
    regime_1h: str | None = None
    regime_4h: str | None = None
    ema_with: dict = field(default_factory=dict)  # "1h20"→bool with-trend
    # Fase B3 — minutos al primer toque por nivel de la grilla (estadístico
    # suficiente para que el visor resuelva el orden SL/TP sin caminar barras):
    t_sl_touch: dict = field(default_factory=dict)  # "2.5" → min | None
    t_tp_touch: dict = field(default_factory=dict)  # "6.0" → min | None
    # B4.3 — toques de pullback por nivel DENTRO de la ventana del estudio
    # (fills de piernas para el modelo de sizing a riesgo y la config
    # combinada B5; solo niveles tocados): "0.5" → min
    t_pb_touch: dict = field(default_factory=dict)

    @property
    def mae_atr(self) -> float | None:
        if self.atr_pct:
            return self.mae_pct / self.atr_pct
        return None

    @property
    def mfe_atr(self) -> float | None:
        if self.atr_pct:
            return self.mfe_pct / self.atr_pct
        return None


def _f(v: str) -> float | None:
    v = (v or "").strip().replace("−", "-")
    if v in ("", "-", "—"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _ts(v: str) -> datetime:
    return datetime.strptime(v.strip()[:16], "%Y-%m-%d %H:%M")


def parse_luxalgo_csv(path: Path) -> list[Trade]:
    """Parsea la lista de operaciones (encabezado con BOM, filas Salida/Entrada
    por Trade number). Devuelve trades ordenados por entry_ts."""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))

    by_num: dict[int, dict[str, dict]] = {}
    for r in rows:
        num = r.get("Trade number")
        tipo = (r.get("Tipo") or "").strip().lower()
        if not num or not tipo:
            continue
        kind = "entry" if tipo.startswith("entrada") else "exit"
        by_num.setdefault(int(num), {})[kind] = r

    trades: list[Trade] = []
    for num, pair in sorted(by_num.items()):
        e = pair.get("entry")
        if e is None:
            continue                    # trade sin fila de entrada: inservible
        x = pair.get("exit")            # puede faltar (trade abierto al final)
        tipo_e = e["Tipo"].strip().lower()
        side = "long" if "largo" in tipo_e else "short"
        pnl_usd = _f(e.get("PyG netas USD", "")) or 0.0
        pnl_pct = _f(e.get("PyG netas %", "")) or 0.0
        mfe = _f(e.get("Desviación favorable %", "")) or 0.0
        mae = _f(e.get("Desviación adversa %", "")) or 0.0
        trades.append(Trade(
            number=num, side=side,
            entry_ts=_ts(e["Fecha y hora"]),
            exit_ts=_ts(x["Fecha y hora"]) if x else None,
            entry_price=_f(e.get("Precio USD", "")) or 0.0,
            exit_price=_f(x.get("Precio USD", "")) if x else None,
            pnl_usd=pnl_usd, pnl_pct=pnl_pct,
            mfe_pct=abs(mfe), mae_pct=abs(mae),
        ))
    trades.sort(key=lambda t: t.entry_ts)
    return trades


def find_trades_csv(instrument: str) -> Path:
    pat = str(TRADES_DIR / f"*_{instrument}1!_*.csv")
    hits = sorted(glob.glob(pat))
    if not hits:
        raise SystemExit(f"No hay CSV para {instrument} ({pat})")
    return Path(hits[-1])


# ---------------------------------------------------------------------------
# OHLC: HOLC CSV (histórico estático) + costura opcional de Postgres (cola)
# ---------------------------------------------------------------------------

def load_holc_from_path(path) -> dict[datetime, tuple]:
    """{DateTime: (open, high, low, close, volume)} de un CSV HOLC concreto
    (formato DateTime,Open,High,Low,Close,Volume). LX-4: lo reusa el snapshot
    por-clave del estudio Luxy (`holc_5m.csv` cosido)."""
    out: dict[datetime, tuple] = {}
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            try:
                ts = datetime.strptime(r["DateTime"].strip(), "%Y-%m-%d %H:%M:%S")
                out[ts] = (float(r["Open"]), float(r["High"]), float(r["Low"]),
                           float(r["Close"]), float(r.get("Volume") or 0))
            except (KeyError, ValueError):
                continue
    if not out:
        raise SystemExit(f"HOLC vacío: {path}")
    return out


def load_holc(sym: str, tf: str = "5m") -> dict[datetime, tuple]:
    """{DateTime: (open, high, low, close, volume)} del export estático global."""
    return load_holc_from_path(_holc_dir() / f"{sym}_{tf}.csv")


# LX-4 — umbral fail-honest del solape HOLC↔DB. Dato de producción: 387,011
# barras verificadas, 1 inconsistente (ruido de feed) = 0.00026%. Por debajo de
# este umbral la costura procede y lo reporta en el manifest; por encima ABORTA
# (datos inconsistentes NO se integran). Constante nombrada = decisión explícita.
STITCH_MAX_INCONSISTENTES_PCT = 0.01        # % (0.01% = 1 en 10.000)
# LX-6 — solape MÍNIMO verificable: sin al menos estas barras en común no se puede
# confiar la alineación de la cola (una cola mal-TZ solapa ~0 keys) → ABORTAR.
STITCH_MIN_OVERLAP_BARS = 12                # ~1h de rejilla 5m

_NY_TZ = None


def _et_naive(dt: datetime) -> datetime:
    """LX-6 — convención canónica ET-naive (la del CSV) al LEER `ohlcv_bars`. Si
    el valor viene tz-aware (columna timestamptz devuelta por Postgres) se
    CONVIERTE a America/New_York y se suelta el tz; si viene naive se asume ya ET
    (nunca `.replace(tzinfo=None)` a ciegas sobre un valor aware)."""
    if dt.tzinfo is None:
        return dt
    global _NY_TZ
    if _NY_TZ is None:
        from zoneinfo import ZoneInfo
        _NY_TZ = ZoneInfo("America/New_York")
    return dt.astimezone(_NY_TZ).replace(tzinfo=None)


async def stitch_from_db(bars: dict[datetime, tuple], sym: str, tf: str
                         ) -> tuple[dict, dict]:
    """⚠ JUBILADA (CSV-only) — YA NO SE LLAMA en el flujo vivo (ni el motor ni el
    Lab cosen; el CSV master es la única fuente de historia). Se conserva como
    código muerto retirado (patrón P3) junto con sus tests; el guardarraíl de
    frescura de `nt_riesgo` reemplaza su fail-closed. NO reintroducir sin revertir
    la decisión de arquitectura del lote CSV-only.

    Cose la cola reciente desde OhlcvBar (Postgres, SOLO lectura) — FAIL-CLOSED
    (LX-6). Normaliza cada `bar_time` a ET-naive (`_et_naive`), valida el solape
    (mismo cierre ±0.1%), y SOLO añade la cola si:
      (a) hay solape suficiente (`checked ≥ STITCH_MIN_OVERLAP_BARS`) — si no,
          ABORTA ("no puedo verificar la alineación de la cola"); una cola mal-TZ
          solapa ~0 keys y cae aquí;
      (b) el % de inconsistentes ≤ umbral (LX-4); si no, ABORTA;
      (c) la cola es CONTINUA: empieza pegada al CSV y no tiene huecos mayores a la
          rejilla de sesión del propio HOLC; si no, ABORTA.
    Devuelve (bars, stats). DB sin barras / sin cola → procede (added 0): la
    costura NO inventa datos, solo extiende con lo que el almacén ya tiene."""
    from sqlalchemy import select
    from app.db.session import AsyncSessionLocal
    from app.models.ohlcv_bar import OhlcvBar

    keys = sorted(bars)
    last_holc = keys[-1]
    # rejilla de sesión = mayor hueco entre barras consecutivas del HOLC (encoda
    # el fin de semana / feriados del instrumento). La cola no puede saltar más.
    session_gap = max((keys[i + 1] - keys[i] for i in range(len(keys) - 1)),
                      default=timedelta(minutes=5))
    last_stitched = last_holc
    mismatched = checked = 0
    cola: list[tuple[datetime, tuple]] = []
    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            select(OhlcvBar).where(OhlcvBar.symbol == sym,
                                   OhlcvBar.timeframe == tf)
        )
        for b in rows.scalars().all():
            ts = _et_naive(b.bar_time)          # LX-6: normalización de TZ
            row = (float(b.open), float(b.high), float(b.low), float(b.close),
                   float(b.volume or 0))
            if ts in bars:                      # solape → validar consistencia
                checked += 1
                if abs(bars[ts][3] - row[3]) > max(0.001 * abs(row[3]), 1e-9):
                    mismatched += 1
                continue
            if ts > last_holc:
                cola.append((ts, row))
    pct = round(100.0 * mismatched / checked, 4) if checked else 0.0
    print(f"   costura DB: cola {len(cola)} barras · solape verificado "
          f"{checked} (inconsistentes: {mismatched} · {pct}%)")

    if cola and checked < STITCH_MIN_OVERLAP_BARS:
        raise SystemExit(
            f"⛔ No puedo verificar la alineación de la cola: solape {checked} < "
            f"{STITCH_MIN_OVERLAP_BARS} barras — NO se cose a ciegas (probable "
            f"desalineación de TZ del feed vs el CSV ET).")
    if pct > STITCH_MAX_INCONSISTENTES_PCT:
        raise SystemExit(
            f"⛔ Solape HOLC↔DB inconsistente: {mismatched}/{checked} = {pct}% "
            f"> {STITCH_MAX_INCONSISTENTES_PCT}% — datos inconsistentes NO se "
            f"integran (revisar TZ/símbolo del feed).")
    cola.sort()
    prev = last_holc
    for ts, _row in cola:
        if ts - prev > session_gap:
            raise SystemExit(
                f"⛔ Salto en la costura: hueco {ts - prev} > rejilla de sesión "
                f"{session_gap} (de {prev} a {ts}) — cola desalineada/incompleta, "
                f"NO se cose.")
        prev = ts
    added = 0
    for ts, row in cola:
        bars[ts] = row
        added += 1
        if ts > last_stitched:
            last_stitched = ts
    stats = {"added": added, "checked": checked, "mismatched": mismatched,
             "pct": pct, "last_holc": last_holc.isoformat(),
             "last_stitched": last_stitched.isoformat()}
    return bars, stats


# ---------------------------------------------------------------------------
# Validación BLOQUEANTE de zona horaria (offset CSV → OHLC)
# ---------------------------------------------------------------------------

def _median_abs_dev(vals: list[float]) -> float:
    med = statistics.median(vals)
    return statistics.median([abs(v - med) for v in vals])


_ROLL_PAIR_MAX_GAP_DAYS = 10    # pares "mismo contrato" para el score local
_NEIGHBORS_FOR_DELTA = 7        # vecinos más cercanos EN TIEMPO para el δ


def _local_dispersion(pts: list[tuple[datetime, float]]) -> float | None:
    """Mediana de |d_i − d_j| entre trades CONSECUTIVOS cercanos en el tiempo
    (gap ≤ 10 días → casi siempre el mismo contrato). Los saltos de nivel por
    roll (constantes por tramos) quedan FUERA del score: en un backtest de baja
    frecuencia (YM ~1 trade/semana, 10 meses, varios rolls) la dispersión
    global mezcla los escalones de roll y ahoga la señal del offset horario."""
    steps = []
    for (ts_a, d_a), (ts_b, d_b) in zip(pts, pts[1:]):
        if abs((ts_b - ts_a).total_seconds()) <= _ROLL_PAIR_MAX_GAP_DAYS * 86400:
            steps.append(abs(d_b - d_a))
    if len(steps) < 5:
        return None
    return statistics.median(steps)


def detect_tz_offset(
    trades: list[Trade], bars: dict[datetime, tuple], sample: int = 60,
) -> tuple[int, float, dict]:
    """Detecta el offset (minutos) que alinea `Fecha y hora` del CSV con el
    DateTime del OHLC. Devuelve (offset_min, sanity, detalle).

    Método: para cada offset candidato, d_i = close(barra en ts+off) − precio_i;
    el offset correcto minimiza la dispersión LOCAL (pares consecutivos con gap
    ≤ 10 días, robusto a los escalones de nivel por roll del continuo
    back-ajustado). Después, sanity = % de precios dentro de [Low,High] de su
    barra tras corregir el nivel con la mediana de los 7 vecinos más cercanos
    EN TIEMPO (método δ de la Memoria §2.C, versión temporal).
    """
    covered = [t for t in trades if t.entry_ts is not None]
    step = max(1, len(covered) // sample)
    sampled = covered[::step][:sample]

    # (score, |offset|, offset): la dispersión local manda; a empate gana el
    # offset más pequeño (series demasiado regulares empatan varios offsets).
    best: tuple[float, int, int] | None = None
    used_fallback = False
    for off in _CANDIDATE_OFFSETS:
        delta = timedelta(minutes=off)
        pts: list[tuple[datetime, float]] = []
        for t in sampled:
            bar = bars.get(t.entry_ts + delta)
            if bar is None:
                continue
            pts.append((t.entry_ts, bar[3] - t.entry_price))
        if len(pts) < max(5, len(sampled) // 2):
            continue                                  # cobertura insuficiente
        score = _local_dispersion(pts)
        if score is None:                             # trades muy espaciados
            score = _median_abs_dev([d for _, d in pts])
            used_fallback = True
        key = (round(score, 6), abs(off), off)
        if best is None or key < best:
            best = key

    if best is None:
        raise SystemExit("⛔ TZ: ningún offset candidato tiene cobertura de barras.")

    score, _absoff, off = best
    delta = timedelta(minutes=off)

    # Sanity con corrección de nivel: δ_i = mediana de los diffs de los 7
    # vecinos más cercanos en tiempo (self incluido — 7 valores diluyen el
    # sesgo de incluirse y aguantan cruzar como mucho un roll).
    seq = [(t, bars.get(t.entry_ts + delta)) for t in sampled]
    seq = [(t, b) for t, b in seq if b is not None]
    raw = [(t.entry_ts, b[3] - t.entry_price) for t, b in seq]
    inside = 0
    for i, (t, b) in enumerate(seq):
        near = sorted(raw, key=lambda p: abs((p[0] - t.entry_ts).total_seconds()))
        d = statistics.median([v for _, v in near[:_NEIGHBORS_FOR_DELTA]])
        _o, h, low, _c, _v = b
        tol = 0.1 * max(h - low, 1e-9)
        if (low - tol) <= (t.entry_price + d) <= (h + tol):
            inside += 1
    sanity = inside / len(seq) if seq else 0.0
    detail = {"offset_minutes": off, "mad": round(score, 4),
              "sanity": round(sanity, 4), "sampled": len(seq),
              "median_level_delta": round(statistics.median([v for _, v in raw]), 2),
              "dispersion_metric": "MAD-global (fallback)" if used_fallback
                                   else "local (pares ≤10d)"}
    return off, sanity, detail


# ---------------------------------------------------------------------------
# Enriquecimiento: ATR(14) en la entrada + hora local del OHLC
# ---------------------------------------------------------------------------

def enrich_with_bars(
    trades: list[Trade], bars: dict[datetime, tuple], offset_min: int,
) -> int:
    """Alinea cada entrada, calcula ATR(14) (lógica viva) y la hora ET.
    Devuelve cuántos trades quedaron SIN cobertura de barras."""
    delta = timedelta(minutes=offset_min)
    keys = sorted(bars)
    index = {ts: i for i, ts in enumerate(keys)}
    uncovered = 0
    for t in trades:
        ts = t.entry_ts + delta
        i = index.get(ts)
        if i is None or i < _ATR_PERIOD + 1:
            uncovered += 1
            continue
        window = [
            {"high": bars[k][1], "low": bars[k][2], "close": bars[k][3]}
            for k in keys[max(0, i - _ATR_LOOKBACK): i + 1]
        ]
        atr = _calc_atr(window, _ATR_PERIOD)
        close = bars[ts][3]
        if atr is None or close <= 0:
            uncovered += 1
            continue
        t.atr_entry = round(atr, 6)
        t.bar_close = close
        t.atr_pct = round(atr / close * 100.0, 6)
        t.hour = ts.hour
        t.aligned_ts = ts
    return uncovered


# LX-13 — barras vecinas toleradas (±N) antes de marcar un trade no_contenido.
# 1 = la barra alineada o sus dos vecinas (cubre el timing de fill de 5m); un
# outlier de frontera de roll cae MUY lejos (cientos de ticks) → fuera igual.
CONTENCION_TRADE_VECINAS = 1


def mark_no_contenido(trades: list[Trade], bars: dict, offset_min: int,
                      tick: float | None = None,
                      vecinas: int = CONTENCION_TRADE_VECINAS) -> list[dict]:
    """LX-13 — marca la contención POR TRADE: `entry_price` dentro de [low,high]
    de su barra alineada o de las ±`vecinas`. Fuera → `no_contenido=True` (+
    `gap_ticks` si hay tick). SOLO trades con barra alineada (los sin cobertura ya
    quedan fuera por ATR; no se tocan). Determinista. Devuelve los no_contenidos
    [{number, entry_ts, gap_ticks}] para el manifest/anexo/ficha.

    Se llama únicamente cuando la contención GLOBAL pasa el umbral (LX-12): si el
    master está globalmente desalineado es un problema de contorno de contrato
    (intrabar_no_confiable), no un puñado de outliers per-trade."""
    delta = timedelta(minutes=offset_min)
    keys = sorted(bars)
    index = {ts: i for i, ts in enumerate(keys)}
    fuera: list[dict] = []
    for t in trades:
        ts = t.entry_ts + delta
        i = index.get(ts)
        if i is None:
            continue                     # sin barra: no aplica (fuera por ATR)
        contenido = False
        for j in range(max(0, i - vecinas), min(len(keys), i + vecinas + 1)):
            _o, h, lo, _c, _v = bars[keys[j]]
            if lo - 1e-12 <= t.entry_price <= h + 1e-12:
                contenido = True
                break
        if not contenido:
            t.no_contenido = True
            close = bars[ts][3]
            t.gap_ticks = round((t.entry_price - close) / tick, 1) if tick else None
            fuera.append({"number": t.number,
                          "entry_ts": t.entry_ts.isoformat(),
                          "gap_ticks": t.gap_ticks})
    return fuera


def split_in_out(trades: list[Trade], oos: float) -> None:
    """Partición temporal: primer (1−oos) in-sample, resto out-of-sample."""
    n = len(trades)
    cut = int(round(n * (1.0 - oos)))
    for i, t in enumerate(trades):
        t.in_sample = i < cut


# ---------------------------------------------------------------------------
# Métricas (línea base y agregación de cualquier lista de desenlaces)
# ---------------------------------------------------------------------------

def feature_rows(trades: list[Trade]) -> list[dict]:
    """Matriz de features por trade — el formato del cache y del núcleo
    compartido (lab_metrics). UNA construcción para reporte, cache y visor."""
    return [{
        "number": t.number, "entry_ts": t.entry_ts.isoformat(),
        "side": t.side, "pnl_pct": t.pnl_pct, "pnl_usd": t.pnl_usd,
        "mae_pct": t.mae_pct, "mfe_pct": t.mfe_pct,
        "atr_entry": t.atr_entry, "atr_pct": t.atr_pct,
        "mae_atr": t.mae_atr, "mfe_atr": t.mfe_atr,
        "hour": t.hour, "in_sample": t.in_sample,
        "sub_volume": t.sub_volume, "sub_atr": t.sub_atr,
        "sub_vwap": t.sub_vwap, "sub_time": t.sub_time,
        "regime_1h": t.regime_1h, "regime_4h": t.regime_4h,
        "ema_with": t.ema_with or None,
        "t_sl_touch": t.t_sl_touch or None,
        "t_tp_touch": t.t_tp_touch or None,
        "t_pb_touch": t.t_pb_touch or None,
    } for t in trades]


def baseline(trades: list[Trade]) -> dict:
    """Línea base vía el núcleo compartido (paridad reporte ↔ visor)."""
    return baseline_from_rows(feature_rows(trades))


# ---------------------------------------------------------------------------
# Fase 2 — features por trade: subscores vivos, régimen 1h/4h, EMA-bias
# ---------------------------------------------------------------------------

def _ema_series(closes: list[float], period: int) -> list[float | None]:
    """EMA clásica (semilla = SMA del primer periodo). None hasta tener datos."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) < period:
        return out
    alpha = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    out[period - 1] = ema
    for i in range(period, len(closes)):
        ema = closes[i] * alpha + ema * (1 - alpha)
        out[i] = ema
    return out


class _TfSeries:
    """Serie de un timeframe (keys ordenadas + closes + EMAs 20/50)."""

    def __init__(self, bars: dict[datetime, tuple]):
        self.keys = sorted(bars)
        self.closes = [bars[k][3] for k in self.keys]
        self.ema = {20: _ema_series(self.closes, 20),
                    50: _ema_series(self.closes, 50)}

    def idx_at(self, ts: datetime) -> int:
        """Índice de la última barra con key ≤ ts (−1 si ninguna)."""
        return bisect_right(self.keys, ts) - 1


def compute_phase2_features(
    trades: list[Trade], bars5: dict[datetime, tuple], instrument: str,
) -> None:
    """Subscores de calidad (funciones VIVAS de quality_scorer), régimen
    (classify_regime, Kaufman ER) en 1h/4h y EMA-bias (1h/4h · 20/50)."""
    keys5 = sorted(bars5)
    idx5 = {k: i for i, k in enumerate(keys5)}
    tf1h = _TfSeries(load_holc(instrument, "1h"))
    tf4h = _TfSeries(load_holc(instrument, "4h"))

    for t in trades:
        if t.aligned_ts is None:
            continue
        i = idx5[t.aligned_ts]
        window = [
            {"high": bars5[k][1], "low": bars5[k][2],
             "close": bars5[k][3], "volume": bars5[k][4]}
            for k in keys5[max(0, i - 99): i + 1]
        ]
        # signal_ts: el HOLC es ET-naive; time_of_day (vivo) espera tz-aware
        # (naive lo trataría como UTC y correría la hora 4-5h).
        ts_utc = t.aligned_ts.replace(tzinfo=_NY).astimezone(_utc_tz.utc)
        sig = SimpleNamespace(
            price=t.bar_close,
            action="buy" if t.side == "long" else "sell",
            signal_ts=ts_utc,
        )
        cfg = {"timezone": "America/New_York"}
        t.sub_volume = round(_SUBSCORES["volume_relative"](sig, window, cfg), 4)
        t.sub_atr = round(_SUBSCORES["atr_normalized"](sig, window, cfg), 4)
        t.sub_vwap = round(_SUBSCORES["vwap_position"](sig, window, cfg), 4)
        t.sub_time = round(_SUBSCORES["time_of_day"](sig, window, cfg), 4)

        for name, tf in (("1h", tf1h), ("4h", tf4h)):
            j = tf.idx_at(t.aligned_ts)
            closes = tf.closes[max(0, j - 249): j + 1] if j >= 0 else []
            setattr(t, f"regime_{name}", classify_regime(closes))
            close_tf = tf.closes[j] if j >= 0 else None
            for period in (20, 50):
                ema = tf.ema[period][j] if j >= 0 else None
                key = f"{name}{period}"
                if close_tf is None or ema is None:
                    t.ema_with[key] = None
                elif t.side == "long":
                    t.ema_with[key] = close_tf > ema
                else:
                    t.ema_with[key] = close_tf < ema


# ---------------------------------------------------------------------------
# Fase 2 — lift de filtros sustractivos (incluir/excluir + re-agregar)
# ---------------------------------------------------------------------------

def filter_lift(trades: list[Trade], keep) -> dict:
    """Aplica un predicado de inclusión a los trades CON features y re-agrega.
    Devuelve in/out + % conservado (los sin cobertura quedan fuera del universo)."""
    universe = [t for t in trades if t.atr_pct is not None]

    def block(sel_in: bool) -> dict:
        base_sel = [t for t in universe if t.in_sample == sel_in]
        kept = [t for t in base_sel if keep(t)]
        m = aggregate([t.pnl_pct for t in kept])
        m["kept_pct"] = (round(100 * len(kept) / len(base_sel), 1)
                         if base_sel else None)
        return m

    return {"in": block(True), "out": block(False)}


def regime_breakdown(trades: list[Trade], tf: str) -> dict[str, dict]:
    """Métricas por valor de régimen (desglose, no gate)."""
    out: dict[str, dict] = {}
    universe = [t for t in trades if t.atr_pct is not None]
    for reg in ("trending_bull", "trending_bear", "ranging", "unknown"):
        sel = [t for t in universe if getattr(t, f"regime_{tf}") == reg]
        if not sel:
            continue
        out[reg] = {
            "in": aggregate([t.pnl_pct for t in sel if t.in_sample]),
            "out": aggregate([t.pnl_pct for t in sel if not t.in_sample]),
            "n": len(sel),
        }
    return out


# ---------------------------------------------------------------------------
# Fase 2 — TP sweep y SL+TP conjunto (orden de toques intrabar en el 5m)
# ---------------------------------------------------------------------------

# §8 barre la grilla completa (B5.2: extendida a nominales altos); el
# conjunto §9 mantiene la subgrilla clásica para no explotar la tabla.
TP_KS = TP_GRID
JOINT_SL_KS = (2.0, 2.5, 4.0, 8.0)
JOINT_TP_KS = (3.0, 4.0, 6.0)


def resim_tp(trades: list[Trade], tp: float) -> dict:
    """TP-only vía el núcleo compartido (paridad reporte ↔ visor)."""
    r = resim_rows(feature_rows(trades), tp=tp)
    return {"in": r["in"], "out": r["out"]}


def touch_minutes(
    t: Trade, keys5: list[datetime], idx5: dict, bars5: dict,
    adverse_lvls: tuple = SL_GRID, favor_lvls: tuple = TP_GRID,
) -> tuple[dict, dict]:
    """UNA caminata por las barras 5m (entrada → salida) registrando el minuto
    del primer toque de cada umbral adverso (SL k·ATR) y favorable (TP tp·ATR).
    Es el estadístico suficiente para el orden de toques: mismo minuto = misma
    barra = ambiguo. Devuelve ({str(k): min|None}, {str(tp): min|None}).

    Consistencia B4.0 con el MFE/MAE de LuxAlgo (misma ATR, misma referencia):
    - Excursión en ABSOLUTO HOLC desde el close de la barra alineada (= el
      precio del instante de entrada EN ESCALA HOLC; usar entry_price del CSV
      contra barras back-ajustadas rompería la escala por el δ del roll).
    - Denominador = entry_price (el del CSV: mfe_pct/mae_pct denominan ahí)
      → (exc/entry)/atr_pct tiene EXACTAMENTE la forma de mfe_atr/mae_atr.
    - La barra alineada se EXCLUYE: HOLC estampa por cierre, así que su rango
      es PRE-entrada (validado en ES real: incluirla sobrecuenta adversos).
    Validación de aceptación en tests/test_lab_consistency.py (ES real)."""
    adv: dict = {str(float(k)): None for k in adverse_lvls}
    fav: dict = {str(float(x)): None for x in favor_lvls}
    if (t.aligned_ts is None or t.bar_close is None
            or not t.entry_price or not t.atr_pct):
        return adv, fav
    end_ts = (t.exit_ts + (t.aligned_ts - t.entry_ts)) if t.exit_ts else None
    i = idx5[t.aligned_ts] + 1          # la barra alineada es pre-entrada
    ref, den = t.bar_close, t.entry_price
    pend_a = set(adv)
    pend_f = set(fav)
    for k5 in keys5[i:]:
        if end_ts is not None and k5 > end_ts:
            break
        if not pend_a and not pend_f:
            break
        _o, high, low, _c, _v = bars5[k5]
        if t.side == "long":
            adverse = (ref - low) / den * 100.0
            favor = (high - ref) / den * 100.0
        else:
            adverse = (high - ref) / den * 100.0
            favor = (ref - low) / den * 100.0
        mins = (k5 - t.aligned_ts).total_seconds() / 60.0
        for key in sorted(pend_a):
            if adverse >= float(key) * t.atr_pct:
                adv[key] = mins
                pend_a.discard(key)
        for key in sorted(pend_f):
            if favor >= float(key) * t.atr_pct:
                fav[key] = mins
                pend_f.discard(key)
    return adv, fav


def be_return_minutes(
    t: Trade, keys5: list[datetime], idx5: dict, bars5: dict,
    triggers: tuple,
) -> dict:
    """Extensión ADITIVA del walk B4.0 (mismo intrabar sancionado que
    `touch_minutes`, misma referencia y misma exclusión de la barra alineada;
    NO cambia el enriched ni reconstruye ruta nueva) — resuelve lo que las
    cachés de PRIMER TOQUE no pueden: el retorno a BREAKEVEN *después* de armar.

    Para cada `trigger`×ATR de la grilla de BE devuelve
    {str(trigger): (minuto, tipo) | None}:
      · ("clean")     retorno a breakeven en una barra ESTRICTAMENTE POSTERIOR
                      a la del armado — el disparo limpio del stop de breakeven.
      · ("same_bar")  la MISMA barra del armado además vuelve a la entrada
                      (subió al trigger y bajó a breakeven): AMBIGUO — dentro de
                      la barra no se conoce el orden. El evaluador lo resuelve
                      pesimista PARA LA PALANCA (ganadora ambigua → recortada a
                      0; perdedora ambigua → conserva su desenlace, no se
                      rescata). Ver `mr_luxy._luxy_exit_atr`.
      · None          nunca arma, o arma y nunca retorna.

    Retorno a 0 = precio de vuelta a la entrada (low ≤ ref en largos / high ≥
    ref en cortos). El toque de 0 ANTES del armado NO cuenta (el precio ondula
    alrededor de la entrada al inicio; solo el retorno post-armado es el disparo
    real del BE) — este es el hueco que las cachés de primer-toque no resuelven.
    """
    out = {str(float(g)): None for g in triggers}
    armed = {str(float(g)): None for g in triggers}
    if (t.aligned_ts is None or t.bar_close is None
            or not t.entry_price or not t.atr_pct):
        return out
    end_ts = (t.exit_ts + (t.aligned_ts - t.entry_ts)) if t.exit_ts else None
    i = idx5[t.aligned_ts] + 1          # la barra alineada es pre-entrada
    ref = t.bar_close
    for k5 in keys5[i:]:
        if end_ts is not None and k5 > end_ts:
            break
        _o, high, low, _c, _v = bars5[k5]
        if t.side == "long":
            favor = (high - ref) / t.entry_price * 100.0
            back_to_be = low <= ref             # volvió (o cruzó) la entrada
        else:
            favor = (ref - low) / t.entry_price * 100.0
            back_to_be = high >= ref
        mins = (k5 - t.aligned_ts).total_seconds() / 60.0
        for g in triggers:
            key = str(float(g))
            if out[key] is not None:
                continue
            if armed[key] is None:
                if favor >= float(g) * t.atr_pct:
                    armed[key] = mins
                    if back_to_be:              # arma y vuelve en la MISMA barra
                        out[key] = (mins, "same_bar")
                continue
            if back_to_be:                       # retorno LIMPIO (barra posterior)
                out[key] = (mins, "clean")
    return out


def compute_touch_times(
    trades: list[Trade], keys5: list[datetime], idx5: dict, bars5: dict,
) -> None:
    """Cachea los toques de la grilla en cada trade (van a la matriz de
    features → el visor resuelve el orden SL/TP sin recompute pesado)."""
    for t in trades:
        t.t_sl_touch, t.t_tp_touch = touch_minutes(t, keys5, idx5, bars5)


def _first_touch(
    t: Trade, k: float, tp: float,
    keys5: list[datetime], idx5: dict, bars5: dict,
) -> str:
    """Orden de toques SL vs TP ("sl"|"tp"|"ambiguous_sl"|"none") — derivado de
    los minutos de toque (misma caminata que el cache del visor)."""
    adv, fav = touch_minutes(t, keys5, idx5, bars5, (k,), (tp,))
    t_sl, t_tp = adv[str(float(k))], fav[str(float(tp))]
    if t_sl is None and t_tp is None:
        return "none"
    if t_tp is None:
        return "sl"
    if t_sl is None:
        return "tp"
    if t_sl == t_tp:
        return "ambiguous_sl"
    return "sl" if t_sl < t_tp else "tp"


def resim_sl_tp(
    trades: list[Trade], k: float, tp: float,
    keys5: list[datetime], idx5: dict, bars5: dict,
) -> dict:
    """SL+TP conjunto vía el núcleo COMPARTIDO (resim_rows): mae/mfe deciden
    QUÉ umbrales se alcanzaron; los toques cacheados deciden el ORDEN."""
    for t in trades:
        if not t.t_sl_touch and t.atr_pct is not None:
            t.t_sl_touch, t.t_tp_touch = touch_minutes(t, keys5, idx5, bars5)
    return resim_rows(feature_rows(trades), sl_k=k, tp=tp)


# ---------------------------------------------------------------------------
# Fase 3 — pullback: profundidad (fill-rate por nivel ×ATR) × desenlace y
# tiempo al pullback (p90 → cancel_after, MISMO estimador que pullback_timing)
# Niveles: PULLBACK_LEVELS vive en lab_metrics (fuente única; B5.2 hasta 10×).
# ---------------------------------------------------------------------------


def pullback_study(
    trades: list[Trade], keys5: list[datetime], idx5: dict, bars5: dict,
    window_min: int = 180, levels: tuple = PULLBACK_LEVELS,
) -> dict[float, dict]:
    """Para cada nivel L×ATR: qué trades lo TOCARON dentro de la ventana de
    entrada (una límite a L habría llenado), a los cuántos minutos, y el
    desenlace NATIVO condicionado a llenó/no-llenó. Referencia de precio =
    close de la barra alineada (el precio del instante de la señal EN ESCALA
    HOLC — la pierna viva se coloca en signalPrice − L×ATR, payload_builder).
    B4.0: la barra alineada se EXCLUYE — HOLC estampa por cierre, su rango es
    PRE-señal y una límite no puede llenarse antes de existir."""
    per: dict[float, dict] = {
        L: {"touch_min": [], "filled": [], "unfilled": []}
        for L in levels
    }
    for t in trades:
        # `not atr_entry` cubre None Y 0.0 (sesión de rango verdadero nulo,
        # p. ej. 6J): sin ATR útil no hay escala — mismo trato que "sin ATR".
        if t.aligned_ts is None or not t.atr_entry or t.bar_close is None:
            continue
        i = idx5[t.aligned_ts] + 1      # la barra alineada es pre-señal
        end = t.aligned_ts + timedelta(minutes=window_min)
        pending = set(levels)
        touched: dict[float, float] = {}
        for k in keys5[i:]:
            if k > end or not pending:
                break
            _o, high, low, _c, _v = bars5[k]
            if t.side == "long":
                adverse_atr = (t.bar_close - low) / t.atr_entry
            else:
                adverse_atr = (high - t.bar_close) / t.atr_entry
            mins = (k - t.aligned_ts).total_seconds() / 60.0
            for L in sorted(pending):
                if adverse_atr >= L:
                    touched[L] = mins
                    pending.discard(L)
        # B4.3 — el toque por trade va al cache (fills de piernas para el
        # modelo de sizing a riesgo del visor; B5 lo reusa).
        t.t_pb_touch = {str(L): m for L, m in sorted(touched.items())}
        for L in levels:
            if L in touched:
                per[L]["touch_min"].append(touched[L])
                per[L]["filled"].append(t)
            else:
                per[L]["unfilled"].append(t)

    out: dict[float, dict] = {}
    for L in levels:
        filled, unfilled = per[L]["filled"], per[L]["unfilled"]
        tm = per[L]["touch_min"]
        total = len(filled) + len(unfilled)
        out[L] = {
            "n_filled": len(filled),
            "fill_rate": round(100 * len(filled) / total, 1) if total else None,
            "t_med": round(pctl(tm, 0.5), 0) if tm else None,
            "t_p90": round(pctl(tm, 0.9), 0) if tm else None,
            # MISMO estimador que el estudio vivo (reconciliación NX-17/NX-28)
            "cancel_after": suggest_cancel_after(tm),
            "filled_outcome": aggregate([t.pnl_pct for t in filled]),
            "unfilled_outcome": aggregate([t.pnl_pct for t in unfilled]),
            "filled_out_exp": aggregate(
                [t.pnl_pct for t in filled if not t.in_sample]
            )["expectancy_pct"],
        }
    return out


# ---------------------------------------------------------------------------
# Fase 2 (cierre) — sobrevivientes out-of-sample entre todos los filtros
# ---------------------------------------------------------------------------

def oos_survivors(base: dict, phase2: dict) -> list[dict]:
    """Filtros×umbral con ΔPF > 0 DENTRO y FUERA de muestra — delega el
    CRITERIO en lab_metrics.survivors_from_lifts (B4.3: el botón "mejor
    configuración" del visor usa la misma función; una sola fuente)."""
    items: list[tuple[str, dict, dict | None]] = []
    for name, by_thr in phase2["subs"].items():
        for thr, d in by_thr.items():
            items.append((f"{name} ≥ {thr}", d, None))
    items += [(f"regime solo {k}", d, None)
              for k, d in phase2["regime_gates"].items()]
    items += [(f"EMA {k[:2]}·{k[2:]} con-tendencia", d, None)
              for k, d in phase2["ema_gates"].items()]
    return survivors_from_lifts(base, items)


def joint_ambiguity_total(phase2: dict) -> int:
    """Barras con SL y TP en la misma barra 5m (conteo del grid conjunto)."""
    total = 0
    for s in phase2["joint"].values():
        for blk in ("in", "out"):
            total += s[blk].get("ambiguous", 0) or 0
    return total


# ---------------------------------------------------------------------------
# SL sweep (re-sim sustractivo del desenlace: Anexo 25 §8.1 punto 5)
# ---------------------------------------------------------------------------

def resim_sl(trades: list[Trade], k: float) -> dict:
    """SL-only vía el núcleo compartido (paridad reporte ↔ visor)."""
    r = resim_rows(feature_rows(trades), sl_k=k)
    for blk in ("in", "out"):
        r[blk]["stopped_pct"] = r[blk].get("sl_pct")   # nombre histórico §2
    return {"in": r["in"], "out": r["out"]}


def hourly_edge(trades: list[Trade]) -> dict[int, dict]:
    """Edge por hora vía el núcleo compartido (paridad reporte ↔ visor)."""
    return hourly_from_rows(feature_rows(trades))


# ---------------------------------------------------------------------------
# Reporte
# ---------------------------------------------------------------------------

def _fmt(v, nd=2):
    if v is None:
        return "—"
    return f"{v:.{nd}f}" if isinstance(v, float) else str(v)


def _lift_row(label: str, d: dict, base: dict) -> str:
    i, o = d["in"], d["out"]
    dpi = (f"{i['pf'] - base['in']['pf']:+.2f}"
           if i["pf"] is not None and base["in"]["pf"] is not None else "—")
    dpo = (f"{o['pf'] - base['out']['pf']:+.2f}"
           if o["pf"] is not None and base["out"]["pf"] is not None else "—")
    warn = " ⚠" if (i["n"] < _LOW_N or o["n"] < _LOW_N) else ""
    return (f"| {label}{warn} | {i['n']} ({_fmt(i.get('kept_pct'),0)}%) | "
            f"{_fmt(i['pf'])} ({dpi}) | {_fmt(i['wr'],1)} | "
            f"{_fmt(i['expectancy_pct'],3)} | {o['n']} "
            f"({_fmt(o.get('kept_pct'),0)}%) | {_fmt(o['pf'])} ({dpo}) | "
            f"{_fmt(o['wr'],1)} | {_fmt(o['expectancy_pct'],3)} |")


_LIFT_HDR = ("| filtro | in n (kept) | in PF (Δ) | in WR% | in exp% | "
             "out n (kept) | out PF (Δ) | out WR% | out exp% |\n"
             "|---|---|---|---|---|---|---|---|---|")


def render_report(instrument: str, csv_path: Path, tz_detail: dict,
                  uncovered: int, base: dict, sweeps: dict[float, dict],
                  hours: dict[int, dict], oos: float, holc_range: tuple,
                  phase2: dict | None = None,
                  pullback: dict | None = None,
                  window_min: int = 180) -> str:
    L: list[str] = []
    L.append(f"# LAB — {instrument} (LuxAlgo nativo vs re-simulación) · "
             f"{datetime.now():%Y-%m-%d %H:%M}")
    L.append("")
    L.append("## 0. Datos y validación de zona horaria (bloqueante)")
    L.append(f"- Trades: `{csv_path.name}` — **{base['total']['n']} trades** "
             f"({'in ' + str(base['in']['n'])} / out {base['out']['n']}, "
             f"split temporal {int((1-oos)*100)}/{int(oos*100)})")
    L.append(f"- OHLC 5m: HOLC {holc_range[0]} → {holc_range[1]}"
             f"{' (+costura DB)' if holc_range[2] else ''}")
    L.append(f"- **Offset TZ detectado: {tz_detail['offset_minutes']:+d} min** "
             f"(CSV → OHLC) · sanity {tz_detail['sanity']*100:.0f}% "
             f"(precio dentro de su barra tras corrección de nivel por roll; "
             f"δ nivel mediano {tz_detail['median_level_delta']:+.2f}) · "
             f"MAD {tz_detail['mad']:.2f} · muestra {tz_detail['sampled']}")
    if uncovered:
        L.append(f"- ⚠ **{uncovered} trade(s) sin cobertura de barras** "
                 f"(posteriores al export HOLC o al inicio): cuentan en la "
                 f"línea base, quedan FUERA del sweep/ATR. En el servidor: "
                 f"`--stitch-db` para coser la cola desde Postgres.")
    L.append("")
    L.append("## 1. Línea base (LuxAlgo nativo — la referencia de TODO)")
    L.append("| bloque | n | WR% | PF | expectancy% | net% | net USD | maxDD% | peor% | p95\\|MAE\\|% | p95 MAE×ATR |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for name in ("in", "out", "total"):
        b = base[name]
        L.append(f"| {name} | {b['n']} | {_fmt(b['wr'],1)} | {_fmt(b['pf'])} | "
                 f"{_fmt(b['expectancy_pct'],3)} | {_fmt(b['net_pct'])} | "
                 f"{_fmt(b['net_usd'])} | {_fmt(b['max_dd_pct'])} | "
                 f"{_fmt(b['worst_pct'])} | {_fmt(b.get('mae_p95_pct'))} | "
                 f"{_fmt(b.get('mae_p95_atr'))} |")
    L.append("")
    L.append("## 2. SL sweep (re-sim: SL ⟺ |mae%| ≥ k·ATR%; desenlace −k·ATR%)")
    L.append("Δ = vs línea base del mismo bloque (solo trades con ATR).")
    L.append("| k×ATR | in n | in PF (Δ) | in WR% | in exp% | in peor% | %SL | out n | out PF (Δ) | out WR% | out exp% | out peor% |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for k in SL_KS:
        s = sweeps[k]
        si, so = s["in"], s["out"]
        dpi = (f"{si['pf'] - base['in']['pf']:+.2f}"
               if si["pf"] is not None and base["in"]["pf"] is not None else "—")
        dpo = (f"{so['pf'] - base['out']['pf']:+.2f}"
               if so["pf"] is not None and base["out"]["pf"] is not None else "—")
        L.append(f"| {k} | {si['n']} | {_fmt(si['pf'])} ({dpi}) | "
                 f"{_fmt(si['wr'],1)} | {_fmt(si['expectancy_pct'],3)} | "
                 f"{_fmt(si['worst_pct'])} | {_fmt(si['stopped_pct'],1)} | "
                 f"{so['n']} | {_fmt(so['pf'])} ({dpo}) | {_fmt(so['wr'],1)} | "
                 f"{_fmt(so['expectancy_pct'],3)} | {_fmt(so['worst_pct'])} |")
    L.append("")
    L.append("## 3. Edge por hora (hora del OHLC = ET; ⚠ = n bajo)")
    L.append("| hora | n | in WR% | in PF | in avg% | out WR% | out PF | out avg% |")
    L.append("|---|---|---|---|---|---|---|---|")
    for h, d in hours.items():
        mark = " ⚠" if d["n"] < _LOW_N else ""
        i, o = d["in"], d["out"]
        L.append(f"| {h:02d}h{mark} | {d['n']} | {_fmt(i['wr'],1)} | "
                 f"{_fmt(i['pf'])} | {_fmt(i['expectancy_pct'],3)} | "
                 f"{_fmt(o['wr'],1)} | {_fmt(o['pf'])} | "
                 f"{_fmt(o['expectancy_pct'],3)} |")
    if phase2:
        L.append("")
        L.append("## 5. Filtros de calidad — lift por subscore y umbral "
                 "(sustractivo; funciones VIVAS de quality_scorer)")
        L.append(_LIFT_HDR)
        for name, by_thr in phase2["subs"].items():
            for thr, d in by_thr.items():
                L.append(_lift_row(f"{name} ≥ {thr}", d, base))
        L.append("")
        L.append("## 6. Régimen (classify_regime, Kaufman ER) — desglose y gates")
        for tf, brk in phase2["regimes"].items():
            L.append(f"**Desglose {tf}:**")
            L.append("| régimen | n | in PF | in WR% | in exp% | out PF | out exp% |")
            L.append("|---|---|---|---|---|---|---|")
            for reg, d in brk.items():
                mark = " ⚠" if d["n"] < _LOW_N else ""
                L.append(f"| {reg}{mark} | {d['n']} | {_fmt(d['in']['pf'])} | "
                         f"{_fmt(d['in']['wr'],1)} | "
                         f"{_fmt(d['in']['expectancy_pct'],3)} | "
                         f"{_fmt(d['out']['pf'])} | "
                         f"{_fmt(d['out']['expectancy_pct'],3)} |")
            L.append("")
        L.append("**Gates de régimen (unknown pasa — semántica viva):**")
        L.append(_LIFT_HDR)
        for label, d in phase2["regime_gates"].items():
            L.append(_lift_row(f"solo {label}", d, base))
        L.append("")
        L.append("## 7. EMA-bias (con-tendencia: long>EMA / short<EMA)")
        L.append(_LIFT_HDR)
        for key, d in phase2["ema_gates"].items():
            L.append(_lift_row(f"EMA {key[:2]} · {key[2:]}", d, base))
        L.append("")
        L.append("## 8. TP sweep (TP ⟺ mfe% ≥ tp·ATR%; desenlace +tp·ATR%)")
        L.append("| tp×ATR | in PF (Δ) | in WR% | in exp% | %TP | out PF (Δ) | out exp% |")
        L.append("|---|---|---|---|---|---|---|")
        for tp, s in phase2["tp"].items():
            i, o = s["in"], s["out"]
            dpi = (f"{i['pf'] - base['in']['pf']:+.2f}"
                   if i["pf"] is not None and base["in"]["pf"] is not None else "—")
            dpo = (f"{o['pf'] - base['out']['pf']:+.2f}"
                   if o["pf"] is not None and base["out"]["pf"] is not None else "—")
            L.append(f"| {tp} | {_fmt(i['pf'])} ({dpi}) | {_fmt(i['wr'],1)} | "
                     f"{_fmt(i['expectancy_pct'],3)} | {_fmt(i.get('tp_pct'),1)} | "
                     f"{_fmt(o['pf'])} ({dpo}) | {_fmt(o['expectancy_pct'],3)} |")
        L.append("")
        L.append("## 9. SL+TP conjunto (orden de toques intrabar en el 5m; "
                 "ambigüedad en la misma barra → SL, conservador)")
        L.append("| k / tp | in PF (Δ) | in exp% | %SL | %TP | amb | out PF (Δ) | out exp% |")
        L.append("|---|---|---|---|---|---|---|---|")
        for (k, tp), s in phase2["joint"].items():
            i, o = s["in"], s["out"]
            dpi = (f"{i['pf'] - base['in']['pf']:+.2f}"
                   if i["pf"] is not None and base["in"]["pf"] is not None else "—")
            dpo = (f"{o['pf'] - base['out']['pf']:+.2f}"
                   if o["pf"] is not None and base["out"]["pf"] is not None else "—")
            L.append(f"| {k}×/{tp}× | {_fmt(i['pf'])} ({dpi}) | "
                     f"{_fmt(i['expectancy_pct'],3)} | {_fmt(i.get('sl_pct'),1)} | "
                     f"{_fmt(i.get('tp_pct'),1)} | {i.get('ambiguous', 0)} | "
                     f"{_fmt(o['pf'])} ({dpo}) | {_fmt(o['expectancy_pct'],3)} |")
    if pullback:
        L.append("")
        L.append(f"## 10. Pullback (ventana de entrada {window_min} min; "
                 f"cancel_after = MISMO estimador que pullback_timing: "
                 f"min(3600, p90·60+60))")
        L.append("| L×ATR | fill% (n) | t med | t p90 | cancel_after s | "
                 "llenó: WR% / PF / avg% | llenó out exp% | no llenó: n / avg% |")
        L.append("|---|---|---|---|---|---|---|---|")
        for lvl, d in pullback.items():
            fo, uo = d["filled_outcome"], d["unfilled_outcome"]
            mark = " ⚠" if d["n_filled"] < _LOW_N else ""
            L.append(
                f"| {lvl}{mark} | {_fmt(d['fill_rate'],1)}% ({d['n_filled']}) | "
                f"{_fmt(d['t_med'],0)}m | {_fmt(d['t_p90'],0)}m | "
                f"{d['cancel_after'] if d['cancel_after'] is not None else '—'} | "
                f"{_fmt(fo['wr'],1)} / {_fmt(fo['pf'])} / "
                f"{_fmt(fo['expectancy_pct'],3)} | "
                f"{_fmt(d['filled_out_exp'],3)} | "
                f"{uo['n']} / {_fmt(uo['expectancy_pct'],3)} |")
    L.append("")
    L.append("## Notas metodológicas")
    L.append("- Filtros = sustractivos (re-agregar); SL/TP = cambian el desenlace (re-sim).")
    L.append("- ATR(14) con la lógica viva (`market_data_service._calc_atr`) sobre las "
             "barras 5m hasta la barra de entrada inclusive; atr% = ATR/close de barra "
             "(escala HOLC; el % del CSV es invariante al offset de roll — Memoria §2.C).")
    L.append("- p95 |MAE| en % del CSV y en múltiplos de ATR (solo cubiertos).")
    L.append("- El re-sim de SL asume disparo intra-trade si el MAE alcanzó el umbral; "
             "el orden SL vs TP (Fase 2) usará el camino intrabar del OHLC 5m.")
    return "\n".join(L) + "\n"


def dump_features(instrument: str, trades: list[Trade],
                  tz_detail: dict | None = None, uncovered: int = 0,
                  pullback: dict | None = None,
                  window_min: int = 180, cache_key: str | None = None,
                  strategy_id: str | None = None) -> Path:
    """B6.1: cache_key = strategy_id cuando el estudio va llaveado por
    estrategia (lab_features_<strategy_id>.json); default = instrumento."""
    rows = feature_rows(trades)
    REPORTES.mkdir(exist_ok=True)
    p = REPORTES / f"lab_features_{cache_key or instrument}.json"
    payload = {
        "meta": {
            "instrument": instrument,
            "strategy_id": strategy_id,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "n_trades": len(rows),
            "uncovered": uncovered,
            "tz": tz_detail or {},
            # Fase B3 — el panel de pullback del visor lee este agregado
            # (calculado offline; el visor no camina barras).
            "pullback": ({str(lvl): d for lvl, d in pullback.items()}
                         if pullback else None),
            "pullback_window_min": window_min,
        },
        "rows": rows,
    }
    p.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

async def run(instrument: str, csv_path: Path | None, oos: float,
              stitch: bool, sample: int, window_min: int = 180,
              strategy_id: str | None = None) -> dict:
    csv_path = csv_path or find_trades_csv(instrument)
    cache_key = strategy_id or instrument
    trades = parse_luxalgo_csv(csv_path)
    if not trades:
        raise SystemExit("CSV sin trades parseables.")
    print(f"· {len(trades)} trades de {csv_path.name}")

    bars = load_holc(instrument, "5m")
    # Costura JUBILADA (CSV-only): el HOLC del CSV master es la única fuente de
    # historia. `stitch` se ignora (bandera vestigial); ver stitch_from_db.
    holc_range = (min(bars), max(bars), False)
    print(f"· {len(bars)} barras 5m ({holc_range[0]} → {holc_range[1]})")

    off, sanity, tz_detail = detect_tz_offset(trades, bars, sample=sample)
    print(f"· TZ offset {off:+d} min · sanity {sanity*100:.0f}%")
    if sanity < _MIN_SANITY:
        raise SystemExit(
            f"⛔ BLOQUEADO: sanity TZ {sanity*100:.0f}% < {_MIN_SANITY*100:.0f}% "
            f"(mejor offset {off:+d} min). No se puede confiar la alineación."
        )

    uncovered = enrich_with_bars(trades, bars, off)
    split_in_out(trades, oos)

    base = baseline(trades)
    sweeps = {k: resim_sl(trades, k) for k in SL_KS}
    hours = hourly_edge(trades)

    # ── Fase 2 (todas las tablas de lift salen del núcleo COMPARTIDO con el
    # visor — lift_from_rows — para que UI y reporte sean idénticos) ──
    compute_phase2_features(trades, bars, instrument)
    keys5 = sorted(bars)
    idx5 = {k: i for i, k in enumerate(keys5)}
    # Toques de la grilla SL/TP (una caminata por trade) — van al cache para
    # que el visor re-simule el orden intrabar sin tocar las barras.
    compute_touch_times(trades, keys5, idx5, bars)
    rows = feature_rows(trades)
    # Grilla de candidatos COMPARTIDA con el visor (lab_metrics — B4.3).
    subs = {
        name: {thr: lift_from_rows(rows, {"subs": {name: thr}})
               for thr in SUB_THRESHOLDS}
        for name in SUB_NAMES
    }
    regimes = {tf: regime_breakdown(trades, tf) for tf in ("1h", "4h")}
    regime_gates = {
        key: lift_from_rows(rows, {"regime": gate})
        for key, gate in REGIME_GATE_DEFS
    }
    ema_gates = {
        key: lift_from_rows(rows, {"ema": [key]})
        for key in EMA_KEYS
    }
    tp_sweeps = {tp: resim_tp(trades, tp) for tp in TP_KS}
    joint = {(k, tp): resim_sl_tp(trades, k, tp, keys5, idx5, bars)
             for k in JOINT_SL_KS for tp in JOINT_TP_KS}
    phase2 = {"subs": subs, "regimes": regimes, "regime_gates": regime_gates,
              "ema_gates": ema_gates, "tp": tp_sweeps, "joint": joint}

    # ── Fase 3 — pullback ──
    pullback = pullback_study(trades, keys5, idx5, bars, window_min)

    report = render_report(instrument, csv_path, tz_detail, uncovered, base,
                           sweeps, hours, oos, holc_range, phase2,
                           pullback, window_min)
    REPORTES.mkdir(exist_ok=True)
    out = REPORTES / f"LAB_{cache_key}_{datetime.now():%Y-%m-%d}.md"
    out.write_text(report, encoding="utf-8")
    feat = dump_features(instrument, trades, tz_detail, uncovered,
                         pullback, window_min, cache_key=cache_key,
                         strategy_id=strategy_id)
    print(f"✅ {out}\n· features: {feat}")
    return {
        "instrument": instrument, "label": cache_key, "report": out,
        "base": base,
        "phase2": phase2, "pullback": pullback, "tz": tz_detail,
        "survivors": oos_survivors(base, phase2),
        "ambiguous": joint_ambiguity_total(phase2),
    }


_INSTRUMENTS = ["ES", "NQ", "RTY", "GC", "CL", "6E", "6J", "YM"]


def _summary_targets() -> list[tuple[str, str, Path | None, str | None]]:
    """(label, instrument, csv, strategy_id) — B6.1: con manifest itera
    ESTRATEGIAS; sin manifest, los 8 instrumentos (retrocompat)."""
    from scripts.lab_manifest import load_manifest

    m = load_manifest()
    entries = (m or {}).get("entries") or {}
    if entries:
        return [(key, e["instrument"], Path(e["csv"]), key)
                for key, e in sorted(entries.items(),
                                     key=lambda kv: (kv[1]["instrument"],
                                                     kv[0]))]
    return [(instr, instr, None, None) for instr in _INSTRUMENTS]


async def run_all_summary(oos: float, stitch: bool, sample: int,
                          window_min: int) -> Path:
    """Corre todas las estrategias del manifest (o los 8 instrumentos sin
    manifest) y escribe el resumen ejecutivo: sobreviviente out-of-sample
    por estrategia (o "nativo domina") + ambigüedad intrabar."""
    targets = _summary_targets()
    results = []
    for label, instr, csv_p, sid in targets:
        print(f"\n===== {label} =====")
        results.append(await run(instr, csv_p, oos, stitch, sample,
                                 window_min, strategy_id=sid))

    L = [f"# LAB — RESUMEN {len(results)} estrategias · "
         f"{datetime.now():%Y-%m-%d %H:%M}",
         "",
         "## Sobrevivientes out-of-sample (ΔPF > 0 dentro Y fuera; "
         "criterio Anexo 25; ⚠ = n_out < 15)",
         "| estrategia | instr | mejor filtro OOS | ΔPF in | ΔPF out | "
         "kept% in | n_out | otros OOS+ | ambigüedad intrabar |",
         "|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        surv = r["survivors"]
        if surv:
            s = surv[0]
            mark = " ⚠" if s["n_out"] < 15 else ""
            L.append(f"| {r['label']} | {r['instrument']} | "
                     f"{s['label']}{mark} | "
                     f"{s['d_in']:+.2f} | {s['d_out']:+.2f} | "
                     f"{_fmt(s['kept_in'],0)} | {s['n_out']} | "
                     f"{len(surv) - 1} | {r['ambiguous']} |")
        else:
            L.append(f"| {r['label']} | {r['instrument']} | "
                     f"**ninguno → nativo domina** | — | "
                     f"— | — | {r['base']['out']['n']} | 0 | {r['ambiguous']} |")
    L.append("")
    L.append("## cancel_after sugerido por estrategia (nivel de diseño más "
             "cercano; estimador de pullback_timing)")
    L.append("| estrategia | fill% @0.75×ATR | cancel_after @0.75 | "
             "fill% @1.5×ATR | cancel_after @1.5 |")
    L.append("|---|---|---|---|---|")
    for r in results:
        p75, p15 = r["pullback"][0.75], r["pullback"][1.5]
        L.append(f"| {r['label']} | {_fmt(p75['fill_rate'],1)}% | "
                 f"{p75['cancel_after'] or '—'} | {_fmt(p15['fill_rate'],1)}% | "
                 f"{p15['cancel_after'] or '—'} |")
    out = REPORTES / f"LAB_RESUMEN_{datetime.now():%Y-%m-%d}.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"\n✅ Resumen: {out}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", choices=_INSTRUMENTS, default=None)
    ap.add_argument("--all-summary", action="store_true",
                    help="corre los 8 instrumentos + resumen ejecutivo")
    ap.add_argument("--csv", type=Path, default=None)
    ap.add_argument("--oos", type=float, default=0.3)
    ap.add_argument("--stitch-db", action="store_true",
                    help="coser la cola reciente desde OhlcvBar (solo lectura)")
    ap.add_argument("--sample", type=int, default=60,
                    help="muestra para la validación TZ")
    ap.add_argument("--pullback-window-min", type=int, default=180,
                    help="ventana de entrada para el estudio de pullback (min)")
    ap.add_argument("--strategy", default=None,
                    help="B6.1: correr UNA estrategia del manifest "
                         "(cache lab_features_<strategy_id>.json)")
    args = ap.parse_args()
    if args.all_summary:
        asyncio.run(run_all_summary(args.oos, args.stitch_db, args.sample,
                                    args.pullback_window_min))
    elif args.strategy:
        from scripts.lab_manifest import load_manifest
        entries = (load_manifest() or {}).get("entries") or {}
        entry = entries.get(args.strategy)
        if entry is None:
            ap.error(f"estrategia {args.strategy!r} no está en el manifest "
                     "(corre `python -m scripts.lab_manifest propose`)")
        asyncio.run(run(entry["instrument"],
                        args.csv or Path(entry["csv"]), args.oos,
                        args.stitch_db, args.sample,
                        args.pullback_window_min,
                        strategy_id=args.strategy))
    elif args.instrument:
        asyncio.run(run(args.instrument, args.csv, args.oos,
                        args.stitch_db, args.sample,
                        args.pullback_window_min))
    else:
        ap.error("usa --strategy <id>, --instrument <SYM> o --all-summary")


if __name__ == "__main__":
    main()
