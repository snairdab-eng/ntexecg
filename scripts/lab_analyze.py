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
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import glob
import json
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

from app.services.market_data_service import _calc_atr  # lógica viva de ATR

HOLC_DIR = Path("NINJATRADER/HOLC")
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

def load_holc(sym: str, tf: str = "5m") -> dict[datetime, tuple]:
    """{DateTime: (open, high, low, close, volume)} del export estático."""
    path = HOLC_DIR / f"{sym}_{tf}.csv"
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


async def stitch_from_db(bars: dict[datetime, tuple], sym: str, tf: str) -> dict:
    """Cose la cola reciente desde OhlcvBar (Postgres, SOLO lectura), validando
    consistencia en el solape (mismo cierre ±1 tick de tolerancia relativa)."""
    from sqlalchemy import select
    from app.db.session import AsyncSessionLocal
    from app.models.ohlcv_bar import OhlcvBar

    last_holc = max(bars)
    added = mismatched = checked = 0
    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            select(OhlcvBar).where(OhlcvBar.symbol == sym,
                                   OhlcvBar.timeframe == tf)
        )
        for b in rows.scalars().all():
            ts = b.bar_time.replace(tzinfo=None) if b.bar_time.tzinfo else b.bar_time
            row = (float(b.open), float(b.high), float(b.low), float(b.close),
                   float(b.volume or 0))
            if ts in bars:                      # solape → validar consistencia
                checked += 1
                if abs(bars[ts][3] - row[3]) > max(0.001 * abs(row[3]), 1e-9):
                    mismatched += 1
                continue
            if ts > last_holc:
                bars[ts] = row
                added += 1
    print(f"   costura DB: +{added} barras nuevas · solape verificado "
          f"{checked} (inconsistentes: {mismatched})")
    if checked and mismatched / checked > 0.05:
        raise SystemExit("⛔ Solape HOLC↔DB inconsistente (>5%) — revisar TZ/símbolo.")
    return bars


# ---------------------------------------------------------------------------
# Validación BLOQUEANTE de zona horaria (offset CSV → OHLC)
# ---------------------------------------------------------------------------

def _median_abs_dev(vals: list[float]) -> float:
    med = statistics.median(vals)
    return statistics.median([abs(v - med) for v in vals])


def detect_tz_offset(
    trades: list[Trade], bars: dict[datetime, tuple], sample: int = 60,
) -> tuple[int, float, dict]:
    """Detecta el offset (minutos) que alinea `Fecha y hora` del CSV con el
    DateTime del OHLC. Devuelve (offset_min, sanity, detalle).

    Método: para cada offset candidato, diffs = close(barra en ts+off) − precio;
    el offset correcto minimiza la dispersión (MAD) — robusto al offset de
    NIVEL por roll del continuo back-ajustado (constante por tramos). Después,
    sanity = % de precios dentro de [Low,High] de su barra tras corregir el
    nivel con la mediana móvil de diffs (±5 vecinos, método de la Memoria §2.C).
    """
    covered = [t for t in trades if t.entry_ts is not None]
    step = max(1, len(covered) // sample)
    sampled = covered[::step][:sample]

    # (mad, |offset|, offset): el MAD manda; a MAD ~igual gana el offset más
    # pequeño (evita elegir un corrimiento absurdo cuando la serie es tan
    # regular que varios offsets empatan).
    best: tuple[float, int, int] | None = None
    for off in _CANDIDATE_OFFSETS:
        delta = timedelta(minutes=off)
        diffs: list[float] = []
        for t in sampled:
            bar = bars.get(t.entry_ts + delta)
            if bar is None:
                continue
            diffs.append(bar[3] - t.entry_price)
        if len(diffs) < max(5, len(sampled) // 2):
            continue                                  # cobertura insuficiente
        mad = round(_median_abs_dev(diffs), 6)
        key = (mad, abs(off), off)
        if best is None or key < best:
            best = key

    if best is None:
        raise SystemExit("⛔ TZ: ningún offset candidato tiene cobertura de barras.")

    mad, _absoff, off = best
    delta = timedelta(minutes=off)

    # Sanity con corrección de nivel (mediana móvil ±5 vecinos)
    seq = [(t, bars.get(t.entry_ts + delta)) for t in sampled]
    seq = [(t, b) for t, b in seq if b is not None]
    raw = [b[3] - t.entry_price for t, b in seq]
    inside = 0
    for i, (t, b) in enumerate(seq):
        lo, hi = max(0, i - 5), min(len(seq), i + 6)
        d = statistics.median(raw[lo:hi])
        o, h, low, c, _v = b
        tol = 0.1 * max(h - low, 1e-9)
        if (low - tol) <= (t.entry_price + d) <= (h + tol):
            inside += 1
    sanity = inside / len(seq) if seq else 0.0
    detail = {"offset_minutes": off, "mad": round(mad, 4),
              "sanity": round(sanity, 4), "sampled": len(seq),
              "median_level_delta": round(statistics.median(raw), 2)}
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
    return uncovered


def split_in_out(trades: list[Trade], oos: float) -> None:
    """Partición temporal: primer (1−oos) in-sample, resto out-of-sample."""
    n = len(trades)
    cut = int(round(n * (1.0 - oos)))
    for i, t in enumerate(trades):
        t.in_sample = i < cut


# ---------------------------------------------------------------------------
# Métricas (línea base y agregación de cualquier lista de desenlaces)
# ---------------------------------------------------------------------------

def aggregate(pnls_pct: list[float], pnls_usd: list[float] | None = None) -> dict:
    n = len(pnls_pct)
    if n == 0:
        return {"n": 0, "wr": None, "pf": None, "expectancy_pct": None,
                "net_pct": None, "net_usd": None, "max_dd_pct": None,
                "worst_pct": None}
    wins = [p for p in pnls_pct if p > 0]
    losses = [p for p in pnls_pct if p < 0]
    gp, gl = sum(wins), abs(sum(losses))
    cum = peak = dd = 0.0
    for p in pnls_pct:
        cum += p
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    return {
        "n": n,
        "wr": round(100 * len(wins) / n, 1),
        "pf": round(gp / gl, 2) if gl > 0 else None,
        "expectancy_pct": round(sum(pnls_pct) / n, 4),
        "net_pct": round(sum(pnls_pct), 2),
        "net_usd": round(sum(pnls_usd), 2) if pnls_usd else None,
        "max_dd_pct": round(dd, 2),
        "worst_pct": round(min(pnls_pct), 2),
    }


def baseline(trades: list[Trade]) -> dict:
    def block(sel: list[Trade]) -> dict:
        m = aggregate([t.pnl_pct for t in sel], [t.pnl_usd for t in sel])
        maes = sorted(t.mae_pct for t in sel)
        if maes:
            k = max(0, int(round(0.95 * (len(maes) - 1))))
            m["mae_p95_pct"] = round(maes[k], 2)
        maes_atr = sorted(t.mae_atr for t in sel if t.mae_atr is not None)
        if maes_atr:
            k = max(0, int(round(0.95 * (len(maes_atr) - 1))))
            m["mae_p95_atr"] = round(maes_atr[k], 2)
        return m

    return {
        "total": block(trades),
        "in": block([t for t in trades if t.in_sample]),
        "out": block([t for t in trades if not t.in_sample]),
    }


# ---------------------------------------------------------------------------
# SL sweep (re-sim sustractivo del desenlace: Anexo 25 §8.1 punto 5)
# ---------------------------------------------------------------------------

def resim_sl(trades: list[Trade], k: float) -> dict:
    """SL activa ⟺ mae% ≥ k·atr% → desenlace = −k·atr%. Solo trades con ATR."""
    def block(sel: list[Trade]) -> dict:
        pnls, stopped = [], 0
        for t in sel:
            if t.atr_pct is None:
                continue
            thr = k * t.atr_pct
            if t.mae_pct >= thr:
                pnls.append(-thr)
                stopped += 1
            else:
                pnls.append(t.pnl_pct)
        m = aggregate(pnls)
        m["stopped_pct"] = round(100 * stopped / len(pnls), 1) if pnls else None
        return m

    return {
        "in": block([t for t in trades if t.in_sample]),
        "out": block([t for t in trades if not t.in_sample]),
    }


def hourly_edge(trades: list[Trade]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    covered = [t for t in trades if t.hour is not None]
    for h in sorted({t.hour for t in covered}):
        sel = [t for t in covered if t.hour == h]
        out[h] = {
            "in": aggregate([t.pnl_pct for t in sel if t.in_sample]),
            "out": aggregate([t.pnl_pct for t in sel if not t.in_sample]),
            "n": len(sel),
        }
    return out


# ---------------------------------------------------------------------------
# Reporte
# ---------------------------------------------------------------------------

def _fmt(v, nd=2):
    if v is None:
        return "—"
    return f"{v:.{nd}f}" if isinstance(v, float) else str(v)


def render_report(instrument: str, csv_path: Path, tz_detail: dict,
                  uncovered: int, base: dict, sweeps: dict[float, dict],
                  hours: dict[int, dict], oos: float, holc_range: tuple) -> str:
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
    L.append("")
    L.append("## 4. Notas metodológicas")
    L.append("- Filtros = sustractivos (re-agregar); SL/TP = cambian el desenlace (re-sim).")
    L.append("- ATR(14) con la lógica viva (`market_data_service._calc_atr`) sobre las "
             "barras 5m hasta la barra de entrada inclusive; atr% = ATR/close de barra "
             "(escala HOLC; el % del CSV es invariante al offset de roll — Memoria §2.C).")
    L.append("- p95 |MAE| en % del CSV y en múltiplos de ATR (solo cubiertos).")
    L.append("- El re-sim de SL asume disparo intra-trade si el MAE alcanzó el umbral; "
             "el orden SL vs TP (Fase 2) usará el camino intrabar del OHLC 5m.")
    return "\n".join(L) + "\n"


def dump_features(instrument: str, trades: list[Trade]) -> Path:
    rows = [{
        "number": t.number, "entry_ts": t.entry_ts.isoformat(),
        "side": t.side, "pnl_pct": t.pnl_pct, "pnl_usd": t.pnl_usd,
        "mae_pct": t.mae_pct, "mfe_pct": t.mfe_pct,
        "atr_entry": t.atr_entry, "atr_pct": t.atr_pct,
        "mae_atr": t.mae_atr, "mfe_atr": t.mfe_atr,
        "hour": t.hour, "in_sample": t.in_sample,
    } for t in trades]
    REPORTES.mkdir(exist_ok=True)
    p = REPORTES / f"lab_features_{instrument}.json"
    p.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

async def run(instrument: str, csv_path: Path | None, oos: float,
              stitch: bool, sample: int) -> Path:
    csv_path = csv_path or find_trades_csv(instrument)
    trades = parse_luxalgo_csv(csv_path)
    if not trades:
        raise SystemExit("CSV sin trades parseables.")
    print(f"· {len(trades)} trades de {csv_path.name}")

    bars = load_holc(instrument, "5m")
    stitched = False
    if stitch:
        bars = await stitch_from_db(bars, instrument, "5m")
        stitched = True
    holc_range = (min(bars), max(bars), stitched)
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

    report = render_report(instrument, csv_path, tz_detail, uncovered, base,
                           sweeps, hours, oos, holc_range)
    REPORTES.mkdir(exist_ok=True)
    out = REPORTES / f"LAB_{instrument}_{datetime.now():%Y-%m-%d}.md"
    out.write_text(report, encoding="utf-8")
    feat = dump_features(instrument, trades)
    print(f"✅ {out}\n· features: {feat}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", required=True,
                    choices=["ES", "NQ", "RTY", "GC", "CL", "6E", "6J", "YM"])
    ap.add_argument("--csv", type=Path, default=None)
    ap.add_argument("--oos", type=float, default=0.3)
    ap.add_argument("--stitch-db", action="store_true",
                    help="coser la cola reciente desde OhlcvBar (solo lectura)")
    ap.add_argument("--sample", type=int, default=60,
                    help="muestra para la validación TZ")
    args = ap.parse_args()
    asyncio.run(run(args.instrument, args.csv, args.oos,
                    args.stitch_db, args.sample))


if __name__ == "__main__":
    main()
