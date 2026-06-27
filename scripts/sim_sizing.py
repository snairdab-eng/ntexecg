#!/usr/bin/env python3
"""
sim_sizing — optimización de cantidades de microcontratos por nivel ATR.

Mantiene fijos los mejores niveles ATR por activo (Anexo 18) y prueba todas las
combinaciones [C1,C2,C3] con total 1..10 micros. C1 en 0×ATR, C2/C3 en los niveles
del activo. Salida nativa LuxAlgo; SL por activo. Métricas en micro $.

Por activo entrega: Top 10 por PF, por Net, por Net/MaxDD, y recomendaciones
conservadora (≤3) / balanceada (≤5) / agresiva (≤10). NO aplica nada en perfiles.

ATR desde ohlcv_bars (--source db, default) o NINJATRADER/HOLC (--source holc).

Uso:  python -m scripts.sim_sizing --source holc        # NTDEV
      python -m scripts.sim_sizing --instr GC,NQ
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import itertools
import os
import re
import numpy as np
import pandas as pd

# (micro, parent, tf, variante, (s,e)|None, sl, (A,B))  — niveles del Anexo 18
V = [
    ("MES", "ES", "5m", "RTH 09:20-15:45", ("09:20", "15:45"), 2.5, (0.75, 1.25)),
    ("MNQ", "NQ", "5m", "24h", None, 8.0, (4, 5)),
    ("MYM", "YM", "15m", "24h", None, 8.0, (1.5, 2)),
    ("M2K", "RTY", "15m", "AM 09:30-12:00", ("09:30", "12:00"), 4.0, (0.5, 1.5)),
    ("M6E", "6E", "5m", "RTH 09:30-15:45", ("09:30", "15:45"), 2.0, (0.5, 0.75)),
    ("MJY", "6J", "5m", "24h", None, 8.0, (2, 3)),
    ("MGC", "GC", "5m", "24h v1", None, 8.0, (0.5, 7)),
    ("MGC", "GC", "5m", "PM 12:00-15:45 v2", ("12:00", "15:45"), 2.5, (1.25, 1.5)),
    ("MGC", "GC", "5m", "RTH 09:30-15:45 v3", ("09:30", "15:45"), 2.5, (0.5, 0.75)),
    ("MCL", "CL", "15m", "24h", None, 8.0, (0.5, 2.5)),
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


def prep(sub, A, B, sl, micro):
    atr = sub.ATR.values.astype(float)
    mae = sub.mae_pts.values.astype(float)
    base = sub.pnl_pts.values.astype(float)
    valid = (~np.isnan(atr)) & (atr > 0)
    stopped = valid & ((mae >= sl * atr) if sl is not None else np.zeros_like(valid))
    fillA = valid & (mae >= A * atr)
    fillB = valid & (mae >= B * atr)
    pnl1 = np.where(stopped, -sl * atr, base) if sl is not None else base.copy()
    v2 = np.where(stopped, -(sl - A) * atr, base + A * atr) if sl is not None else base + A * atr
    v3 = np.where(stopped, -(sl - B) * atr, base + B * atr) if sl is not None else base + B * atr
    return dict(pnl1=pnl1 * micro, v2=(v2 * fillA) * micro, v3=(v3 * fillB) * micro,
                fillA=fillA.astype(float), fillB=fillB.astype(float), stopped=stopped)


def evalc(P, c1, c2, c3):
    pnl = c1 * P["pnl1"] + c2 * P["v2"] + c3 * P["v3"]
    filled = c1 + c2 * P["fillA"] + c3 * P["fillB"]
    active = filled > 0
    na = int(active.sum())
    w, ll = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    eq = np.cumsum(pnl)
    dd = (np.maximum.accumulate(eq) - eq).max() if len(pnl) else 0
    stops = int((P["stopped"] & active).sum())
    return dict(net=float(pnl.sum()), pf=(w / ll if ll > 0 else float("inf")),
                wr=100 * (pnl > 0).sum() / na if na else 0, worst=float(pnl.min()) if len(pnl) else 0,
                maxdd=float(dd), avgc=float(filled.mean()), stops=stops, pct=100 * stops / na if na else 0)


def pf(x):
    return "inf" if x == float("inf") else f"{x:.2f}"


def ndmd(m):
    return m["net"] / m["maxdd"] if m["maxdd"] > 0 else (float("inf") if m["net"] > 0 else 0)


async def run(source, only):
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

    def tbl(title, rk, f1, f2, f10, nat):
        print(f"\n**{title}**")
        print("| C1-C2-C3 | tot | avgC | Net | PF | WR | Peor | MaxDD | stops | %st | Δf1 | Δf2 | Δf10 | Δnat |")
        print("|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|")
        for m in rk[:10]:
            print(f"| {m['c1']}-{m['c2']}-{m['c3']} | {m['tot']} | {m['avgc']:.2f} | {m['net']:,.0f} | "
                  f"{pf(m['pf'])} | {m['wr']:.0f} | {m['worst']:,.0f} | {m['maxdd']:,.0f} | {m['stops']} | "
                  f"{m['pct']:.0f}% | {m['net']-f1:+,.0f} | {m['net']-f2:+,.0f} | {m['net']-f10:+,.0f} | {m['net']-nat:+,.0f} |")

    for micro_l, sym, tf, var, w, sl, (A, B) in V:
        if only and sym not in only:
            continue
        t, micro = await get(sym, tf)
        sub = t if w is None else t[t.dt.apply(lambda d: inw(d, w[0], w[1]))]
        if len(sub) == 0:
            continue
        P = prep(sub, A, B, sl, micro)
        Pn = prep(sub, A, B, None, micro)
        f1 = evalc(P, 1, 0, 0)["net"]
        nat = evalc(Pn, 1, 0, 0)["net"]
        f2, f10 = f1 * 2, f1 * 10
        rows = []
        for c1, c2, c3 in itertools.product(range(11), repeat=3):
            tot = c1 + c2 + c3
            if 1 <= tot <= 10:
                m = evalc(P, c1, c2, c3)
                m.update(c1=c1, c2=c2, c3=c3, tot=tot)
                rows.append(m)
        print(f"\n\n## {sym}→{micro_l} · {var} · SL {sl}x · niveles 0-{A}-{B} · n={len(sub)} · micro=${micro:,.2f}/pt")
        print(f"baseline — nativo1c {nat:,.0f} · fijo1 {f1:,.0f} · fijo2 {f2:,.0f} · fijo10 {f10:,.0f}")
        tbl("Top 10 PF", sorted([m for m in rows if m["net"] > 0], key=lambda m: (-m["pf"], m["maxdd"], -m["worst"])), f1, f2, f10, nat)
        tbl("Top 10 Net", sorted(rows, key=lambda m: -m["net"]), f1, f2, f10, nat)
        tbl("Top 10 Net/MaxDD", sorted([m for m in rows if m["net"] > 0], key=lambda m: -ndmd(m)), f1, f2, f10, nat)
        cons = sorted([m for m in rows if m["tot"] <= 3 and m["net"] > 0], key=lambda m: (-m["pf"], m["maxdd"], -m["worst"]))
        bal = sorted([m for m in rows if m["tot"] <= 5 and m["net"] > 0], key=lambda m: (-ndmd(m), -m["net"]))
        agg = sorted(rows, key=lambda m: -m["net"])
        print("\n**Recomendaciones**")
        print("| Perfil | C1-C2-C3 | tot | Net | PF | Peor | MaxDD | avgC |")
        print("|---|---|--:|--:|--:|--:|--:|--:|")
        for nm, lst in (("Conservadora", cons), ("Balanceada", bal), ("Agresiva", agg)):
            if lst:
                m = lst[0]
                print(f"| {nm} | {m['c1']}-{m['c2']}-{m['c3']} | {m['tot']} | {m['net']:,.0f} | "
                      f"{pf(m['pf'])} | {m['worst']:,.0f} | {m['maxdd']:,.0f} | {m['avgc']:.2f} |")


def main():
    ap = argparse.ArgumentParser(description="Optimización de cantidades por nivel ATR")
    ap.add_argument("--source", choices=["db", "holc"], default="db")
    ap.add_argument("--instr", default="", help="símbolos parent (ES,NQ,...)")
    args = ap.parse_args()
    only = {s.strip().upper() for s in args.instr.split(",") if s.strip()}
    asyncio.run(run(args.source, only))


if __name__ == "__main__":
    main()
