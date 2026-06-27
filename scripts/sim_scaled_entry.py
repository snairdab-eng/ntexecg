#!/usr/bin/env python3
"""
sim_scaled_entry — mejores niveles ATR de compra escalonada por instrumento.

Objetivo (paso previo al sizing): hallar en qué múltiplos de ATR conviene agregar
Compra 2 y Compra 3 ANTES de tocar el SL del instrumento. 1 microcontrato por nivel
(sin optimizar cantidades todavía). Salida principal = nativa de LuxAlgo.

Modelo (Anexo 14): por trade, con ATR(14) en el TF propio y MAE medido:
  - C1 siempre en 0×ATR. C2 llena si MAE≥A×ATR; C3 si MAE≥B×ATR (A<B<SL).
  - Si MAE≥SL×ATR → posición detenida: cada contrato sale en −(SL−nivel)×ATR.
  - Si no → salida nativa: cada contrato gana pnl_nativo + nivel×ATR (mejor entrada).
ATR desde ohlcv_bars (NTEXECG, --source db, default) o NINJATRADER/HOLC (--source holc).

Uso:  python -m scripts.sim_scaled_entry              # todos, top combos
      python -m scripts.sim_scaled_entry --source holc
      python -m scripts.sim_scaled_entry --instr GC,NQ --top 10
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import os
import re
import numpy as np
import pandas as pd

# (micro, parent, tf, winname, (start,end)|None, sl|None)  — ventana y SL candidato por activo
ROWS = [
    ("MES", "ES", "5m", "RTH 09:20-15:45", ("09:20", "15:45"), 2.5),
    ("MNQ", "NQ", "5m", "24h", None, 8.0),
    ("MYM", "YM", "15m", "24h", None, 8.0),
    ("M2K", "RTY", "15m", "RTH 09:30-15:45", ("09:30", "15:45"), 4.0),
    ("M2K", "RTY", "15m", "RTH 09:30-15:45", ("09:30", "15:45"), 6.0),
    ("M2K", "RTY", "15m", "RTH 09:30-15:45", ("09:30", "15:45"), 8.0),
    ("M2K", "RTY", "15m", "AM 09:30-12:00", ("09:30", "12:00"), 4.0),
    ("M2K", "RTY", "15m", "AM 09:30-12:00", ("09:30", "12:00"), 8.0),
    ("M6E", "6E", "5m", "RTH 09:30-15:45", ("09:30", "15:45"), 2.0),
    ("M6E", "6E", "5m", "RTH 09:30-15:45", ("09:30", "15:45"), 8.0),
    ("M6E", "6E", "5m", "AM 09:30-12:00", ("09:30", "12:00"), 8.0),
    ("MJY", "6J", "5m", "24h", None, 8.0),
    ("MGC", "GC", "5m", "24h", None, 8.0),
    ("MGC", "GC", "5m", "PM 12:00-15:45", ("12:00", "15:45"), 2.5),
    ("MGC", "GC", "5m", "RTH 09:30-15:45", ("09:30", "15:45"), 2.5),
    ("MCL", "CL", "15m", "24h", None, None),   # nativo (sin SL) — referencia
    ("MCL", "CL", "15m", "24h", None, 4.0),
    ("MCL", "CL", "15m", "24h", None, 8.0),
]


def watr(df, p=14):
    h, l, c = df["High"], df["Low"], df["Close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / p, adjust=False, min_periods=p).mean()


def load_trades(path):
    df = pd.read_csv(path, encoding="utf-8-sig")
    df.columns = [c.strip() for c in df.columns]
    C = dict(n="Trade number", t="Tipo", dt="Fecha y hora", px="Precio USD",
             mae="Desviación adversa USD", pnl="PyG netas USD")
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
                      mae_pts=abs(float(e[C["mae"]])), pnl_usd=float(e[C["pnl"]])))
    return pd.DataFrame(R).sort_values("dt").reset_index(drop=True)


async def bars_db(symbol, timeframe):
    from sqlalchemy import select
    from app.db.session import AsyncSessionLocal
    from app.models.ohlcv_bar import OhlcvBar
    from app.services.bar_store import PROVIDER
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(OhlcvBar.bar_time, OhlcvBar.high, OhlcvBar.low, OhlcvBar.close)
            .where(OhlcvBar.symbol == symbol, OhlcvBar.timeframe == timeframe,
                   OhlcvBar.provider == PROVIDER).order_by(OhlcvBar.bar_time))
        rows = res.all()
    df = pd.DataFrame(rows, columns=["DateTime", "High", "Low", "Close"])
    for c in ("High", "Low", "Close"):
        df[c] = df[c].astype(float)
    df["DateTime"] = pd.to_datetime(df["DateTime"]).dt.tz_localize(None)
    return df


def bars_holc(symbol, timeframe):
    df = pd.read_csv(f"NINJATRADER/HOLC/{symbol}_{timeframe}.csv", encoding="utf-8-sig")
    df.columns = [c.strip().lstrip("﻿") for c in df.columns]
    df["DateTime"] = pd.to_datetime(df["DateTime"]).dt.tz_localize(None)
    return df[["DateTime", "High", "Low", "Close"]]


def inw(d, s, e):
    hm = d.hour * 60 + d.minute
    return (int(s[:2]) * 60 + int(s[3:])) <= hm <= (int(e[:2]) * 60 + int(e[3:]))


def levels_for(sl):
    if sl is None:
        return [0.5, 1, 1.5, 2, 2.5, 3, 4, 5, 6]
    if sl <= 3:
        out, x = [], 0.5
        while x <= sl - 0.25 + 1e-9:
            out.append(round(x, 2)); x += 0.25
        return out
    return [x for x in [0.5, 1, 1.5, 2, 2.5, 3, 4, 5, 6, 7] if x <= sl - 0.5 + 1e-9]


def sim(sub, levels, sl, micro):
    pnls, contracts, stops = [], [], 0
    for _, r in sub.iterrows():
        a = r.ATR
        if pd.isna(a) or a <= 0:
            pnls.append(r.pnl_pts * micro); contracts.append(1); continue
        mae, base = r.mae_pts, r.pnl_pts
        filled = [0.0] + [L for L in levels if mae >= L * a]
        if sl is not None and mae >= sl * a:
            stops += 1
            pl = sum(-(sl - L) * a for L in filled)
        else:
            pl = sum(base + L * a for L in filled)
        pnls.append(pl * micro); contracts.append(len(filled))
    pnl = np.array(pnls)
    n = len(pnl)
    w, ll = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    eq = np.cumsum(pnl)
    dd = (np.maximum.accumulate(eq) - eq).max() if n else 0
    return dict(net=pnl.sum(), pf=(w / ll if ll > 0 else float("inf")),
                wr=100 * (pnl > 0).mean() if n else 0, worst=pnl.min() if n else 0,
                maxdd=dd, avgc=float(np.mean(contracts)) if contracts else 0,
                stops=stops, pct=100 * stops / n if n else 0, n=n)


def pf(x):
    return "inf" if x == float("inf") else f"{x:.2f}"


async def run(source, only, top):
    cache = {}

    async def get(sym, tf):
        if (sym, tf) in cache:
            return cache[(sym, tf)]
        path = [p for p in glob.glob("ListaDeOperaciones/*.csv")
                if re.search(rf"_{sym}1!_", os.path.basename(p))][0]
        t = load_trades(path)
        pv = float(np.median([abs(r.pnl_usd) / abs(r.pnl_pts) for r in t.itertuples() if r.pnl_pts != 0]))
        micro = pv / 10
        t["mae_pts"] = t["mae_pts"] / pv
        bars = await bars_db(sym, tf) if source == "db" else bars_holc(sym, tf)
        bars = bars.sort_values("DateTime").reset_index(drop=True)
        bars["ATR"] = watr(bars)
        t = pd.merge_asof(t.sort_values("dt"), bars[["DateTime", "ATR"]],
                          left_on="dt", right_on="DateTime", direction="backward")
        cache[(sym, tf)] = (t, micro)
        return cache[(sym, tf)]

    print(f"# Compra escalonada — mejores niveles ATR por activo (ATR={source}, 1 micro/nivel)\n")
    for micro_l, sym, tf, wn, w, sl in ROWS:
        if only and sym not in only:
            continue
        t, micro = await get(sym, tf)
        sub = t if w is None else t[t.dt.apply(lambda d: inw(d, w[0], w[1]))]
        if len(sub) == 0:
            continue
        nat = sim(sub, [], None, micro)        # nativo 1 contrato sin SL
        f1 = sim(sub, [], sl, micro)            # fijo-1 con SL
        f2 = f1["net"] * 2                       # fijo-2
        lv = levels_for(sl)
        combos = []
        for i in range(len(lv)):
            for j in range(i + 1, len(lv)):
                m = sim(sub, [lv[i], lv[j]], sl, micro)
                m["A"], m["B"] = lv[i], lv[j]
                combos.append(m)
        combos.sort(key=lambda m: -m["net"])
        sll = "nativo(sinSL)" if sl is None else f"SL {sl}x"
        print(f"\n## {sym}→{micro_l} [{tf}] · {wn} · {sll} · n={len(sub)} · micro=${micro:,.2f}/pt")
        print(f"  baseline: Nativo1={nat['net']:,.0f} (peor {nat['worst']:,.0f}) · "
              f"Fijo-1={f1['net']:,.0f} · Fijo-2={f2:,.0f}")
        print("| 0-A-B | Net | PF | WR | Peor | MaxDD | %stop | avgC | Δf1 | Δf2 | Δnat |")
        print("|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|")
        for m in combos[:top]:
            print(f"| 0-{m['A']}-{m['B']} | {m['net']:,.0f} | {pf(m['pf'])} | {m['wr']:.0f} | "
                  f"{m['worst']:,.0f} | {m['maxdd']:,.0f} | {m['pct']:.0f}% | {m['avgc']:.2f} | "
                  f"{m['net']-f1['net']:+,.0f} | {m['net']-f2:+,.0f} | {m['net']-nat['net']:+,.0f} |")


def main():
    ap = argparse.ArgumentParser(description="Mejores niveles ATR de compra escalonada")
    ap.add_argument("--source", choices=["db", "holc"], default="db")
    ap.add_argument("--instr", default="", help="símbolos parent (ES,NQ,...)")
    ap.add_argument("--top", type=int, default=6, help="combos a mostrar por fila")
    args = ap.parse_args()
    only = {s.strip().upper() for s in args.instr.split(",") if s.strip()}
    asyncio.run(run(args.source, only, args.top))


if __name__ == "__main__":
    main()
