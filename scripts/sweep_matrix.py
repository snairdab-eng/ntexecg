#!/usr/bin/env python3
"""
Matriz de simulación de SLs por instrumento y ventana (decisión data-driven).
Para cada instrumento (TF propio) y cada ventana operativa, simula 7 escenarios
(Nativo, 2.0, 2.5, 3.0, 4.0, 6.0, 8.0 ×ATR; TP 6×ATR) con la regla conservadora
del Anexo 11 y reporta, en micro $:
  Net, PF, WR, Peor trade, MaxDD, #stops, %stop, Δ vs nativo, Δ vs config actual(2.0×).

Usa ATR(14) Wilder real por barra en el TF de la estrategia (HOLC locales) y los
trade-lists autorizados de ListaDeOperaciones/.

Uso:  python -m scripts.sweep_matrix          # imprime markdown
      python -m scripts.sweep_matrix > REPORTES/sim.md
"""
import glob
import os
import re
import numpy as np
import pandas as pd

# token -> (símbolo HOLC, TF, [(nombre_ventana, (start,end) | None=24h)])
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
MICRO_LABEL = {"ES": "MES", "NQ": "MNQ", "YM": "MYM", "RTY": "M2K",
               "GC": "MGC", "CL": "MCL", "6E": "M6E", "6J": "MJY"}
SCEN = [("Nativo", None), ("2.0x", 2.0), ("2.5x", 2.5), ("3.0x", 3.0),
        ("4.0x", 4.0), ("6.0x", 6.0), ("8.0x", 8.0)]
CURRENT = 2.0  # sl_atr_multiplier por defecto del seed


def watr(df, p=14):
    h, l, c = df["High"], df["Low"], df["Close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / p, adjust=False, min_periods=p).mean()


def load(path):
    df = pd.read_csv(path, encoding="utf-8-sig")
    df.columns = [c.strip() for c in df.columns]
    C = dict(n="Trade number", t="Tipo", dt="Fecha y hora", px="Precio USD",
             mfe="Desviación favorable USD", mae="Desviación adversa USD", pnl="PyG netas USD")
    df[C["dt"]] = pd.to_datetime(df[C["dt"]])
    R = []
    for tn, g in df.groupby(C["n"]):
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


def inw(d, s, e):
    hm = d.hour * 60 + d.minute
    return (int(s[:2]) * 60 + int(s[3:])) <= hm <= (int(e[:2]) * 60 + int(e[3:]))


def simulate(sub, k, micro):
    if k is None:
        pts = sub.pnl_pts.values
        stops = 0
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
                wr=100 * (pnl > 0).mean() if n else 0,
                worst=pnl.min() if n else 0, maxdd=dd, stops=stops,
                pct=100 * stops / n if n else 0, n=n)


def pf(x):
    return "inf" if x == float("inf") else f"{x:.2f}"


def main():
    out = ["# Matriz de simulación de SLs por instrumento y ventana — micro $\n",
           "Escenarios: Nativo · 2.0× · 2.5× · 3.0× · 4.0× · 6.0× · 8.0×ATR (TP 6×ATR).",
           "Config actual seed = 2.0×. Δnat = net−Nativo. Δact = net−2.0×. ATR(14) real en TF propio.\n"]
    for path in sorted(glob.glob("ListaDeOperaciones/*.csv")):
        tok = next((t for t in INSTR if re.search(rf"_{re.escape(t)}!_", os.path.basename(path))), None)
        if not tok:
            continue
        sym, tf, wins = INSTR[tok]
        t = load(path)
        pv = float(np.median([abs(r.pnl_usd) / abs(r.pnl_pts) for r in t.itertuples() if r.pnl_pts != 0]))
        micro = pv / 10.0
        t["mae_pts"] = t["mae_pts"] / pv
        t["mfe_pts"] = t["mfe_pts"] / pv
        b = pd.read_csv(f"NINJATRADER/HOLC/{sym}_{tf}.csv", encoding="utf-8-sig")
        b.columns = [c.strip().lstrip("﻿") for c in b.columns]
        b["DateTime"] = pd.to_datetime(b["DateTime"])
        b = b.sort_values("DateTime")
        b["ATR"] = watr(b)
        t = pd.merge_asof(t.sort_values("dt"), b[["DateTime", "ATR"]],
                          left_on="dt", right_on="DateTime", direction="backward")
        out.append(f"\n## {sym}→{MICRO_LABEL[sym]} [{tf}]  micro=${micro:,.2f}/pt  "
                   f"ATRmed={t.ATR.median():.4g}  trades={len(t)}")
        for wn, w in wins:
            sub = t if w is None else t[t.dt.apply(lambda d: inw(d, w[0], w[1]))]
            if len(sub) == 0:
                continue
            nat = simulate(sub, None, micro)
            cur = simulate(sub, CURRENT, micro)
            out.append(f"\n**{wn}** (n={len(sub)})\n")
            out.append("| Escenario | Net $ | PF | WR% | Peor $ | MaxDD $ | #stops | %stop | Δnat | Δact |")
            out.append("|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|")
            for nm, k in SCEN:
                m = simulate(sub, k, micro)
                out.append(f"| {nm} | {m['net']:,.0f} | {pf(m['pf'])} | {m['wr']:.0f} | "
                           f"{m['worst']:,.0f} | {m['maxdd']:,.0f} | {m['stops']} | {m['pct']:.0f}% | "
                           f"{m['net']-nat['net']:+,.0f} | {m['net']-cur['net']:+,.0f} |")
    print("\n".join(out))


if __name__ == "__main__":
    main()
