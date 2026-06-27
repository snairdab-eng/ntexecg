#!/usr/bin/env python3
"""
sim_sl_matrix — Simulación de SLs por instrumento/ventana DESDE NTEXECG.

Corre la matriz de escenarios de SL usando:
  • los trade-lists autorizados de LuxAlgo en ListaDeOperaciones/, y
  • el ATR(14) Wilder calculado sobre las barras propias de NTEXECG (tabla
    `ohlcv_bars`, la misma fuente que usa el gateway) — ruta de producción.

Es la versión "directo desde NTEXECG": el ATR sale de la BD, no de un CSV externo.
Con --source holc usa los CSV de NINJATRADER/HOLC (idéntica data, sin DB).

Escenarios: Nativo (sin SL) · 2.0× · 2.5× · 3.0× · 4.0× · 6.0× · 8.0×ATR · TP 6×ATR
(regla conservadora Anexo 11: MAE≥k×ATR ⇒ −k×ATR; si no, resultado nativo, ganancia
capada a +TP si MFE≥TP). Métricas en micro $ ($/pt÷10). Config actual seed = 2.0×.

Uso (repo root, venv activo):
    python -m scripts.sim_sl_matrix                  # ATR desde ohlcv_bars (NTEXECG)
    python -m scripts.sim_sl_matrix --source holc    # ATR desde NINJATRADER/HOLC/*.csv
    python -m scripts.sim_sl_matrix --instr NQ,GC    # solo algunos
    python -m scripts.sim_sl_matrix --ks 2,2.5,3,4,6,8
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import os
import re
import numpy as np
import pandas as pd

# token CSV -> (símbolo parent en ohlcv_bars/HOLC, TF de la estrategia, ventanas)
INSTR = {
    "ES1": ("ES", "5m", [("24h", None), ("RTH 09:20-15:45", ("09:20", "15:45"))]),
    "NQ1": ("NQ", "5m", [("24h", None), ("RTH 09:30-15:45", ("09:30", "15:45"))]),
    "YM1": ("YM", "15m", [("24h", None), ("RTH 09:30-15:45", ("09:30", "15:45"))]),
    "RTY1": ("RTY", "15m", [("24h", None), ("RTH 09:30-15:45", ("09:30", "15:45")),
                            ("AM 09:30-12:00", ("09:30", "12:00"))]),
    "GC1": ("GC", "5m", [("24h", None), ("RTH 09:30-15:45", ("09:30", "15:45"))]),
    "CL1": ("CL", "15m", [("24h", None), ("RTH 09:30-15:45", ("09:30", "15:45"))]),
    "6E1": ("6E", "5m", [("24h", None), ("RTH 09:30-15:45", ("09:30", "15:45")),
                         ("AM 09:30-12:00", ("09:30", "12:00"))]),
    "6J1": ("6J", "5m", [("24h", None), ("RTH 09:30-15:45", ("09:30", "15:45"))]),
}
MICRO = {"ES": "MES", "NQ": "MNQ", "YM": "MYM", "RTY": "M2K",
         "GC": "MGC", "CL": "MCL", "6E": "M6E", "6J": "MJY"}
SCEN = [("Nativo", None), ("2.0x", 2.0), ("2.5x", 2.5), ("3.0x", 3.0),
        ("4.0x", 4.0), ("6.0x", 6.0), ("8.0x", 8.0)]
CURRENT = 2.0


def wilder_atr(df, p=14):
    h, l, c = df["High"], df["Low"], df["Close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / p, adjust=False, min_periods=p).mean()


def load_trades(path):
    df = pd.read_csv(path, encoding="utf-8-sig")
    df.columns = [c.strip() for c in df.columns]
    C = dict(n="Trade number", t="Tipo", dt="Fecha y hora", px="Precio USD",
             mfe="Desviación favorable USD", mae="Desviación adversa USD", pnl="PyG netas USD")
    df[C["dt"]] = pd.to_datetime(df[C["dt"]])
    R = []
    for _, g in df.groupby(C["n"]):
        e = g[g[C["t"]].str.contains("Entrada", case=False, na=False)]
        x = g[g[C["t"]].str.contains("Salida", case=False, na=False)]
        if e.empty or x.empty:
            continue
        e, x = e.iloc[0], x.iloc[0]
        lng = "largo" in str(e[C["t"]]).lower()
        ep, xp = float(e[C["px"]]), float(x[C["px"]])
        R.append(dict(dt=e[C["dt"]], pnl_pts=(xp - ep) if lng else (ep - xp),
                      mae_pts=abs(float(e[C["mae"]])), mfe_pts=abs(float(e[C["mfe"]])),
                      pnl_usd=float(e[C["pnl"]])))
    return pd.DataFrame(R).sort_values("dt").reset_index(drop=True)


async def bars_from_db(symbol, timeframe):
    """OHLC desde ohlcv_bars (NTEXECG). bar_time naive ET."""
    from sqlalchemy import select
    from app.db.session import AsyncSessionLocal
    from app.models.ohlcv_bar import OhlcvBar
    from app.services.bar_store import PROVIDER
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(OhlcvBar.bar_time, OhlcvBar.high, OhlcvBar.low, OhlcvBar.close)
            .where(OhlcvBar.symbol == symbol, OhlcvBar.timeframe == timeframe,
                   OhlcvBar.provider == PROVIDER)
            .order_by(OhlcvBar.bar_time)
        )
        rows = res.all()
    df = pd.DataFrame(rows, columns=["DateTime", "High", "Low", "Close"])
    for col in ("High", "Low", "Close"):
        df[col] = df[col].astype(float)
    df["DateTime"] = pd.to_datetime(df["DateTime"]).dt.tz_localize(None)
    return df


def bars_from_holc(symbol, timeframe):
    df = pd.read_csv(f"NINJATRADER/HOLC/{symbol}_{timeframe}.csv", encoding="utf-8-sig")
    df.columns = [c.strip().lstrip("﻿") for c in df.columns]
    df["DateTime"] = pd.to_datetime(df["DateTime"]).dt.tz_localize(None)
    return df[["DateTime", "High", "Low", "Close"]]


def attach_atr(trades, bars):
    b = bars.sort_values("DateTime").reset_index(drop=True)
    b["ATR"] = wilder_atr(b)
    t = trades.copy()
    t["dt"] = pd.to_datetime(t["dt"]).dt.tz_localize(None)
    t = t.sort_values("dt")
    return pd.merge_asof(t, b[["DateTime", "ATR"]], left_on="dt",
                         right_on="DateTime", direction="backward")


def inw(d, s, e):
    hm = d.hour * 60 + d.minute
    return (int(s[:2]) * 60 + int(s[3:])) <= hm <= (int(e[:2]) * 60 + int(e[3:]))


def simulate(sub, k, micro):
    if k is None:
        pts, stops = sub.pnl_pts.values, 0
    else:
        pts, stops = [], 0
        for _, r in sub.iterrows():
            a = r.ATR
            if pd.isna(a) or a <= 0:
                pts.append(r.pnl_pts); continue
            sl, tp = k * a, 6 * a
            if r.mae_pts >= sl:
                pts.append(-sl); stops += 1
            elif r.mfe_pts >= tp:
                pts.append(tp)
            else:
                pts.append(r.pnl_pts)
        pts = np.array(pts)
    pnl = np.array(pts) * micro
    n = len(pnl)
    w, l = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    eq = np.cumsum(pnl)
    dd = (np.maximum.accumulate(eq) - eq).max() if n else 0
    return dict(net=pnl.sum(), pf=(w / l if l > 0 else float("inf")),
                wr=100 * (pnl > 0).mean() if n else 0, worst=pnl.min() if n else 0,
                maxdd=dd, stops=stops, pct=100 * stops / n if n else 0, n=n)


def pf(x):
    return "inf" if x == float("inf") else f"{x:.2f}"


async def run(source, only, ks):
    scen = [("Nativo", None)] + [(f"{k:g}x", k) for k in ks] if ks else SCEN
    print(f"# Simulación de SLs DESDE NTEXECG (ATR source = {source}) — micro $")
    print("Δnat = net−Nativo · Δact = net−2.0× · ATR(14) Wilder en TF propio.\n")
    for path in sorted(glob.glob("ListaDeOperaciones/*.csv")):
        tok = next((t for t in INSTR if re.search(rf"_{re.escape(t)}!_", os.path.basename(path))), None)
        if not tok:
            continue
        sym, tf, wins = INSTR[tok]
        if only and sym not in only:
            continue
        t = load_trades(path)
        pv = float(np.median([abs(r.pnl_usd) / abs(r.pnl_pts) for r in t.itertuples() if r.pnl_pts != 0]))
        micro = pv / 10.0
        t["mae_pts"] = t["mae_pts"] / pv
        t["mfe_pts"] = t["mfe_pts"] / pv
        bars = await bars_from_db(sym, tf) if source == "db" else bars_from_holc(sym, tf)
        if bars.empty:
            print(f"\n## {sym}: SIN barras en {source} para {tf} — omitido")
            continue
        t = attach_atr(t, bars)
        print(f"\n## {sym}→{MICRO[sym]} [{tf}]  micro=${micro:,.2f}/pt  "
              f"ATRmed={t.ATR.median():.4g}  trades={len(t)}  barras={len(bars)}")
        for wn, w in wins:
            sub = t if w is None else t[t.dt.apply(lambda d: inw(d, w[0], w[1]))]
            if len(sub) == 0:
                continue
            nat = simulate(sub, None, micro)
            cur = simulate(sub, CURRENT, micro)
            print(f"\n**{wn}** (n={len(sub)})")
            print("| Escenario | Net $ | PF | WR% | Peor $ | MaxDD $ | #stops | %stop | Δnat | Δact |")
            print("|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|")
            for nm, k in scen:
                m = simulate(sub, k, micro)
                print(f"| {nm} | {m['net']:,.0f} | {pf(m['pf'])} | {m['wr']:.0f} | "
                      f"{m['worst']:,.0f} | {m['maxdd']:,.0f} | {m['stops']} | {m['pct']:.0f}% | "
                      f"{m['net']-nat['net']:+,.0f} | {m['net']-cur['net']:+,.0f} |")


def main():
    ap = argparse.ArgumentParser(description="Simulación de SLs desde NTEXECG")
    ap.add_argument("--source", choices=["db", "holc"], default="db",
                    help="db = ohlcv_bars (NTEXECG, default); holc = CSVs locales")
    ap.add_argument("--instr", default="", help="lista de símbolos parent (ES,NQ,...)")
    ap.add_argument("--ks", default="", help="lista de multiplicadores (2,2.5,3,4,6,8)")
    args = ap.parse_args()
    only = {s.strip().upper() for s in args.instr.split(",") if s.strip()}
    ks = [float(x) for x in args.ks.split(",") if x.strip()] if args.ks else None
    asyncio.run(run(args.source, only, ks))


if __name__ == "__main__":
    main()
