#!/usr/bin/env python3
"""
Calibracion de SL por ATR a partir del trade-list de LuxAlgo + barras HOLC locales.
Replica la metodologia del Anexo 11 (ES) pero usando ATR(14) Wilder REAL por barra
(no proxy fijo). Parametrizable por instrumento.

Uso:
  python -m scripts.calibrate_sl_from_trades \
      --trades "ListaDeOperaciones/...NQ1!...csv" \
      --bars   "NINJATRADER/HOLC/NQ_5m.csv" \
      --point-value 20 --tz-shift 0

Reglas (Anexo 11, conservadoras):
  - SL = k x ATR(entrada). TP = 6 x ATR(entrada).
  - Si MAE >= SL  -> trade detenido en -SL (SL antes que TP si ambos se tocan).
  - elif MFE >= TP -> +TP.
  - else           -> resultado nativo real del trade.
"""
import argparse
import sys
import pandas as pd
import numpy as np


def wilder_atr(df, period=14):
    h, l, c = df["High"], df["Low"], df["Close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    # Wilder smoothing (RMA)
    atr = tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    return atr


def load_trades(path, point_value):
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    # Columnas esperadas (en espanol del export LuxAlgo)
    col_n = "Trade number"
    col_tipo = "Tipo"
    col_dt = "Fecha y hora"
    col_px = "Precio USD"
    col_mfe = "Desviación favorable USD"
    col_mae = "Desviación adversa USD"
    df[col_dt] = pd.to_datetime(df[col_dt])
    trades = []
    for tn, g in df.groupby(col_n):
        entry = g[g[col_tipo].str.contains("Entrada", case=False, na=False)]
        exit_ = g[g[col_tipo].str.contains("Salida", case=False, na=False)]
        if entry.empty or exit_.empty:
            continue
        entry = entry.iloc[0]
        exit_ = exit_.iloc[0]
        is_long = "largo" in str(entry[col_tipo]).lower()
        ep = float(entry[col_px])
        xp = float(exit_[col_px])
        pnl_pts = (xp - ep) if is_long else (ep - xp)
        mae_pts = abs(float(entry[col_mae])) / point_value
        mfe_pts = abs(float(entry[col_mfe])) / point_value
        trades.append({
            "trade": int(tn),
            "entry_dt": entry[col_dt],
            "dir": "long" if is_long else "short",
            "entry_px": ep,
            "exit_px": xp,
            "pnl_pts": pnl_pts,
            "mae_pts": mae_pts,
            "mfe_pts": mfe_pts,
        })
    return pd.DataFrame(trades).sort_values("entry_dt").reset_index(drop=True)


def attach_atr(trades, bars, tz_shift=0):
    bars = bars.copy()
    bars["DateTime"] = pd.to_datetime(bars["DateTime"])
    bars = bars.sort_values("DateTime").reset_index(drop=True)
    bars["ATR"] = wilder_atr(bars, 14)
    # merge_asof: ATR de la barra <= hora de entrada (point-in-time)
    t = trades.copy()
    t["entry_dt_adj"] = t["entry_dt"] + pd.Timedelta(hours=tz_shift)
    t = t.sort_values("entry_dt_adj")
    merged = pd.merge_asof(
        t, bars[["DateTime", "ATR"]],
        left_on="entry_dt_adj", right_on="DateTime", direction="backward",
    )
    merged = merged.sort_values("entry_dt").reset_index(drop=True)
    return merged


def in_window(dt, start, end):
    """start/end como 'HH:MM'. Soporta ventanas normales (no cruzan medianoche)."""
    hm = dt.hour * 60 + dt.minute
    s = int(start[:2]) * 60 + int(start[3:])
    e = int(end[:2]) * 60 + int(end[3:])
    return s <= hm <= e


def simulate(trades, k, tp_mult=6.0):
    res = []
    for _, r in trades.iterrows():
        atr = r["ATR"]
        if pd.isna(atr) or atr <= 0:
            res.append(r["pnl_pts"])  # sin ATR: resultado nativo
            continue
        sl = k * atr
        tp = tp_mult * atr
        if r["mae_pts"] >= sl:
            res.append(-sl)
        elif r["mfe_pts"] >= tp:
            res.append(tp)
        else:
            res.append(r["pnl_pts"])
    return np.array(res)


def metrics(pnl_pts, point_value):
    pnl = pnl_pts * point_value
    n = len(pnl)
    if n == 0:
        return dict(n=0, wr=0, pf=0, exp=0, net=0, maxdd=0)
    wins = pnl[pnl > 0].sum()
    losses = -pnl[pnl < 0].sum()
    wr = 100.0 * (pnl > 0).sum() / n
    pf = (wins / losses) if losses > 0 else float("inf")
    exp = pnl.mean()
    net = pnl.sum()
    eq = np.cumsum(pnl)
    peak = np.maximum.accumulate(eq)
    dd = peak - eq
    maxdd = dd.max() if n else 0
    return dict(n=n, wr=wr, pf=pf, exp=exp, net=net, maxdd=maxdd)


def fmt(m):
    pf = "inf" if m["pf"] == float("inf") else f"{m['pf']:.2f}"
    return f"n={m['n']:>3}  WR={m['wr']:5.1f}%  PF={pf:>5}  exp=${m['exp']:>8.0f}  net=${m['net']:>9.0f}  maxDD=${m['maxdd']:>9.0f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", required=True)
    ap.add_argument("--bars", required=True)
    ap.add_argument("--point-value", type=float, default=20.0)
    ap.add_argument("--tz-shift", type=float, default=0.0,
                    help="horas a sumar a la hora del trade para alinear con las barras")
    ap.add_argument("--ks", default="1.5,2.0,2.5,3.0,4.0,5.0,6.0,8.0")
    args = ap.parse_args()

    ks = [float(x) for x in args.ks.split(",")]
    trades = load_trades(args.trades, args.point_value)
    bars = pd.read_csv(args.bars)
    trades = attach_atr(trades, bars, args.tz_shift)

    print(f"== Trades: {len(trades)} | point_value=${args.point_value:g}/pt | tz_shift={args.tz_shift:g}h ==")
    print(f"   ATR(14) real en entradas: media={trades['ATR'].mean():.1f} pt  "
          f"mediana={trades['ATR'].median():.1f} pt  min={trades['ATR'].min():.1f}  max={trades['ATR'].max():.1f}")
    nan_atr = trades["ATR"].isna().sum()
    if nan_atr:
        print(f"   (aviso: {nan_atr} trades sin ATR -> usan resultado nativo)")

    windows = {
        "24h":            None,
        "RTH 09:30-15:45": ("09:30", "15:45"),
        "AM 09:30-12:00":  ("09:30", "12:00"),
        "PM 12:00-15:45":  ("12:00", "15:45"),
        "Overnight (fuera RTH)": "OUTSIDE_RTH",
    }

    for wname, w in windows.items():
        if w is None:
            sub = trades
        elif w == "OUTSIDE_RTH":
            mask = ~trades["entry_dt"].apply(lambda d: in_window(d, "09:30", "15:45"))
            sub = trades[mask]
        else:
            mask = trades["entry_dt"].apply(lambda d: in_window(d, w[0], w[1]))
            sub = trades[mask]
        print(f"\n--- Ventana: {wname}  ({len(sub)} trades) ---")
        # nativo (sin SL)
        m = metrics(sub["pnl_pts"].values, args.point_value)
        print(f"  nativo (sin SL)  {fmt(m)}")
        for k in ks:
            pnl = simulate(sub, k)
            m = metrics(pnl, args.point_value)
            cut = int(((sub['mae_pts'].values >= k * sub['ATR'].values)).sum())
            print(f"  k={k:<4}          {fmt(m)}  | stops={cut}")


if __name__ == "__main__":
    main()
