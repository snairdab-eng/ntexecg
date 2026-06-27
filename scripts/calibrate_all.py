#!/usr/bin/env python3
"""
Barrido de SL por ATR(14) real sobre TODOS los trade-lists de ListaDeOperaciones/.
Usa el ATR(14) Wilder en el TIMEFRAME PROPIO de cada estrategia (no todo 5m),
auto-detecta el $/pt del CSV y verifica la escala contra los HOLC.
Produce detalle por instrumento + tabla maestra.

Uso:  python -m scripts.calibrate_all
"""
import glob
import os
import re
import traceback
import numpy as np
import pandas as pd

# token -> (simbolo HOLC, etiqueta, timeframe de la estrategia)
INSTR = {
    "ES1": ("ES", "ES→MES", "5m"),   "NQ1": ("NQ", "NQ→MNQ", "5m"),
    "YM1": ("YM", "YM→MYM", "15m"),  "RTY1": ("RTY", "RTY→M2K", "15m"),
    "GC1": ("GC", "GC→MGC", "5m"),   "CL1": ("CL", "CL→MCL", "15m"),
    "6E1": ("6E", "6E→M6E", "5m"),   "6J1": ("6J", "6J→MJY", "5m"),
}
KS = [1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0]
WINS = {"24h": None, "RTH 09:30-15:45": ("09:30", "15:45"),
        "AM 09:30-12:00": ("09:30", "12:00"), "OVN(noRTH)": "OUT"}


def wilder_atr(df, p=14):
    h, l, c = df["High"], df["Low"], df["Close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / p, adjust=False, min_periods=p).mean()


def load_trades(path):
    df = pd.read_csv(path, encoding="utf-8-sig")
    df.columns = [c.strip() for c in df.columns]
    C = dict(n="Trade number", tipo="Tipo", dt="Fecha y hora", px="Precio USD",
             mfe="Desviación favorable USD", mae="Desviación adversa USD", pnl="PyG netas USD")
    df[C["dt"]] = pd.to_datetime(df[C["dt"]])
    rows = []
    for tn, g in df.groupby(C["n"]):
        ent = g[g[C["tipo"]].str.contains("Entrada", case=False, na=False)]
        ext = g[g[C["tipo"]].str.contains("Salida", case=False, na=False)]
        if ent.empty or ext.empty:
            continue
        ent, ext = ent.iloc[0], ext.iloc[0]
        lng = "largo" in str(ent[C["tipo"]]).lower()
        ep, xp = float(ent[C["px"]]), float(ext[C["px"]])
        rows.append(dict(entry_dt=ent[C["dt"]], entry_px=ep,
                         pnl_pts=(xp - ep) if lng else (ep - xp),
                         mae_usd=abs(float(ent[C["mae"]])), mfe_usd=abs(float(ent[C["mfe"]])),
                         pnl_usd=float(ent[C["pnl"]])))
    return pd.DataFrame(rows).sort_values("entry_dt").reset_index(drop=True)


def attach(t, bars):
    b = bars.copy()
    b.columns = [c.strip().lstrip("﻿") for c in b.columns]
    b["DateTime"] = pd.to_datetime(b["DateTime"])
    b = b.sort_values("DateTime").reset_index(drop=True)
    b["ATR"] = wilder_atr(b)
    t = t.sort_values("entry_dt").reset_index(drop=True)
    return pd.merge_asof(t, b[["DateTime", "ATR"]], left_on="entry_dt",
                         right_on="DateTime", direction="backward")


def inw(dt, s, e):
    hm = dt.hour * 60 + dt.minute
    return (int(s[:2]) * 60 + int(s[3:])) <= hm <= (int(e[:2]) * 60 + int(e[3:]))


def sim(sub, k, tpm=6.0):
    o = []
    for _, r in sub.iterrows():
        a = r["ATR"]
        if pd.isna(a) or a <= 0:
            o.append(r["pnl_pts"]); continue
        sl, tp = k * a, tpm * a
        o.append(-sl if r["mae_pts"] >= sl else (tp if r["mfe_pts"] >= tp else r["pnl_pts"]))
    return np.array(o)


def met(pp, pv):
    pnl = np.asarray(pp) * pv
    n = len(pnl)
    if n == 0:
        return dict(n=0, pf=0, net=0, maxdd=0)
    w, l = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    eq = np.cumsum(pnl); dd = np.maximum.accumulate(eq) - eq
    return dict(n=n, pf=(w / l) if l > 0 else float("inf"), net=pnl.sum(), maxdd=dd.max())


def pfs(x):
    return "inf" if x == float("inf") else f"{x:.2f}"


def main():
    files = sorted(glob.glob("ListaDeOperaciones/*.csv"))
    master = []
    for path in files:
        base = os.path.basename(path)
        tok = next((t for t in INSTR if re.search(rf"_{re.escape(t)}!_", base)), None)
        if not tok:
            continue
        sym, label, tf = INSTR[tok]
        try:
            t = load_trades(path)
            pv = float(np.median([abs(r.pnl_usd) / abs(r.pnl_pts)
                                  for r in t.itertuples() if r.pnl_pts != 0]))
            t["mae_pts"] = t["mae_usd"] / pv
            t["mfe_pts"] = t["mfe_usd"] / pv
            bars = pd.read_csv(f"NINJATRADER/HOLC/{sym}_{tf}.csv", encoding="utf-8-sig")
            t = attach(t, bars)
            bc = bars.copy(); bc.columns = [c.strip().lstrip("﻿") for c in bc.columns]
            bd = pd.to_datetime(bc["DateTime"])
            br = bc[(bd >= t.entry_dt.min()) & (bd <= t.entry_dt.max())]
            scale = (t.entry_px.median() / br["Close"].median()) if len(br) else float("nan")
            warn = "  ⚠ESCALA" if not (0.9 < scale < 1.1) else ""
            print(f"\n{'='*76}\n{label} [{tf}] trades={len(t)} pv=${pv:,.0f} "
                  f"ATRmed={t.ATR.median():.4g} escala={scale:.3f}{warn}")
            row = dict(instr=label, tf=tf, n=len(t))
            for wn, w in WINS.items():
                if w is None:
                    sub = t
                elif w == "OUT":
                    sub = t[~t.entry_dt.apply(lambda d: inw(d, "09:30", "15:45"))]
                else:
                    sub = t[t.entry_dt.apply(lambda d: inw(d, w[0], w[1]))]
                nat = met(sub.pnl_pts.values, pv)
                bk, bm = None, None
                for k in KS:
                    m = met(sim(sub, k), pv)
                    if bm is None or m["pf"] > bm["pf"]:
                        bm, bk = m, k
                print(f"   {wn:<16} n={nat['n']:>3} nativo PF={pfs(nat['pf']):>5} "
                      f"net=${nat['net']:>11,.0f} | mejor k={bk} PF={pfs(bm['pf']):>5} "
                      f"net=${bm['net']:>11,.0f}")
                if wn == "24h":
                    row.update(pf24=nat["pf"], net24=nat["net"], bk=bk, bpf=bm["pf"])
                elif wn.startswith("RTH"):
                    row.update(pfrth=nat["pf"])
                elif wn.startswith("AM"):
                    row.update(pfam=nat["pf"])
                else:
                    row.update(pfovn=nat["pf"], netovn=nat["net"])
            master.append(row)
        except Exception as e:
            print(f"\n!! ERROR {label}: {type(e).__name__}: {e}")
            traceback.print_exc()

    print(f"\n\n{'#'*84}\nTABLA MAESTRA (ATR real en TF de cada estrategia)\n{'#'*84}")
    h = (f"{'Instr':<9}{'TF':>4}{'n':>4}{'PF24h':>7}{'PF_RTH':>7}{'PF_AM':>7}"
         f"{'PF_OVN'