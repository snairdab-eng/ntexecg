import bisect, csv, statistics as st, sys
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo
sys.path.insert(0, ".")
from app.services.quality_scorer import _SUBSCORES, QualityScorer
from app.services.hmm_service import classify_regime

NY = ZoneInfo("America/New_York")
HOLC = "NINJATRADER/HOLC"; LIST = "ListaDeOperaciones"
INSTR = {
    "NQ": {"tf": "5m", "csv": "LuxAlgo®_-_Backtester_(S&O)_[3.3.3]_CME_MINI_NQ1!_2026-06-27_b65ed.csv"},
    "YM": {"tf": "15m", "csv": "LuxAlgo®_-_Backtester_(S&O)_[3.3.3]_CBOT_MINI_YM1!_2026-06-27_373a4.csv"},
    "GC": {"tf": "5m", "csv": "LuxAlgo®_-_Backtester_(S&O)_[3.3.3]_COMEX_GC1!_2026-06-27_c6548.csv"},
}
SUBS = ("volume_relative", "atr_normalized", "vwap_position", "time_of_day")

def load_bars(sym, tf):
    rows, dts = [], []
    with open(f"{HOLC}/{sym}_{tf}.csv", encoding="utf-8-sig") as f:
        for r in csv.reader(f):
            if r[0].lower().startswith("date") or not r[0].strip(): continue
            try:
                dt = datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S")
                rows.append({"high": float(r[2]), "low": float(r[3]), "close": float(r[4]),
                             "volume": float(r[5]), "open": float(r[1])}); dts.append(dt)
            except (ValueError, IndexError): continue
    return rows, dts

def load_trades(path):
    out = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.reader(f):
            if not r or r[0].strip().lower().startswith("trade"): continue
            tipo = r[1].strip().lower()
            if not tipo.startswith("entrada"): continue
            try:
                ts = datetime.strptime(r[2].strip(), "%Y-%m-%d %H:%M")
                out.append({"dir": "buy" if "largo" in tipo else "sell", "ts": ts,
                            "price": float(r[4]), "pnl": float(r[7])})
            except (ValueError, IndexError): continue
    return out

def pf(pnls):
    g = sum(p for p in pnls if p > 0); l = sum(-p for p in pnls if p < 0)
    return (g / l) if l > 0 else float("inf")

def stats(trades):
    pnls = [t["pnl"] for t in trades]
    if not pnls: return dict(n=0, win=0.0, net=0.0, pf=0.0, avg=0.0)
    wins = sum(1 for p in pnls if p > 0)
    return dict(n=len(pnls), win=100*wins/len(pnls), net=sum(pnls), pf=pf(pnls), avg=sum(pnls)/len(pnls))

def fmt(s):
    pfs = "inf" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
    return f"n={s['n']:3d}  win={s['win']:5.1f}%  net=${s['net']:>9,.0f}  PF={pfs:>5}  avg=${s['avg']:>7,.0f}"

def evaluate(sym, cfg, report):
    bars, dts = load_bars(sym, cfg["tf"]); bars1h, dts1h = load_bars(sym, "1h")
    trades = load_trades(f"{LIST}/{cfg['csv']}")
    cfgq = {"timezone": "America/New_York"}
    for t in trades:
        idx = bisect.bisect_right(dts, t["ts"]) - 1; t["_idx"] = idx
        t["_dc"] = (bars[idx]["close"] - t["price"]) if 0 <= idx < len(bars) else 0.0
    for k, t in enumerate(trades):
        win = [trades[j]["_dc"] for j in range(max(0, k-5), min(len(trades), k+6))]
        t["adj_price"] = t["price"] + st.median(win)
    enriched, sane = [], 0
    for t in trades:
        idx = t["_idx"]
        if idx < 21: continue
        window = bars[max(0, idx-99): idx+1]
        sig = SimpleNamespace(price=t["adj_price"], action=t["dir"],
                              signal_ts=t["ts"].replace(tzinfo=NY), timeframe=cfg["tf"])
        sub = {n: _SUBSCORES[n](sig, window, cfgq) for n in SUBS}
        comp = round(sum(sub.values()) / len(sub) * 100)
        i1 = bisect.bisect_right(dts1h, t["ts"]) - 1
        reg = classify_regime([b["close"] for b in bars1h[:i1+1]])
        eb = bars[idx]
        if eb["low"]-1e-9 <= t["adj_price"] <= eb["high"]+1e-9: sane += 1
        enriched.append({**t, "sub": sub, "comp": comp, "reg": reg})
    base = stats(enriched)
    report.append(f"\n{'='*78}\n{sym}  ({cfg['tf']})  -  regimen 1h\n{'='*78}")
    report.append(f"Baseline: {fmt(base)}")
    report.append(f"Sanity tz/precio (adj_price dentro de barra): {100*sane/max(1,len(enriched)):.0f}% de {len(enriched)}")
    report.append("\n-- Lift por filtro (Q1 bajo vs Q4 alto) --")
    for k in list(SUBS) + ["comp"]:
        vals = sorted((t["sub"][k] if k in SUBS else t["comp"]) for t in enriched)
        if len(vals) < 8: continue
        q1 = vals[len(vals)//4]; q3 = vals[3*len(vals)//4]
        lo = [t for t in enriched if (t["sub"][k] if k in SUBS else t["comp"]) <= q1]
        hi = [t for t in enriched if (t["sub"][k] if k in SUBS else t["comp"]) >= q3]
        sl, sh = stats(lo), stats(hi)
        report.append(f"  {k:16s} Q1 win={sl['win']:5.1f}% avg=${sl['avg']:>7,.0f}  | Q4 win={sh['win']:5.1f}% avg=${sh['avg']:>7,.0f}")
    report.append("\n-- Sweep score_minimum (composite, 4 filtros peso igual) --")
    report.append(f"  {'thr':>4}  {'kept':>5}  {'blk':>4}  {'win%':>6}  {'net':>11}  {'PF':>5}  {'dNet':>10}")
    for thr in (50,55,60,65,70,75,80,85):
        kept = [t for t in enriched if t["comp"] >= thr]; s = stats(kept)
        pfs = "inf" if s["pf"]==float("inf") else f"{s['pf']:.2f}"
        report.append(f"  {thr:>4}  {s['n']:>5}  {base['n']-s['n']:>4}  {s['win']:>5.1f}  ${s['net']:>9,.0f}  {pfs:>5}  ${s['net']-base['net']:>+9,.0f}")
    report.append("\n-- Regimen 1h (Kaufman ER) en la entrada --")
    g = lambda p: stats([t for t in enriched if p(t)])
    al = g(lambda t:(t["dir"]=="buy" and t["reg"]=="trending_bull") or (t["dir"]=="sell" and t["reg"]=="trending_bear"))
    co = g(lambda t:(t["dir"]=="buy" and t["reg"]=="trending_bear") or (t["dir"]=="sell" and t["reg"]=="trending_bull"))
    rg = g(lambda t: t["reg"]=="ranging"); un = g(lambda t: t["reg"]=="unknown")
    report.append(f"  trend-aligned : {fmt(al)}")
    report.append(f"  counter-trend : {fmt(co)}")
    report.append(f"  ranging       : {fmt(rg)}")
    report.append(f"  unknown       : {fmt(un)}")
    bc = stats([t for t in enriched if not ((t["dir"]=="buy" and t["reg"]=="trending_bear") or (t["dir"]=="sell" and t["reg"]=="trending_bull"))])
    report.append(f"  -> bloquear counter-trend: {fmt(bc)} (dNet ${bc['net']-base['net']:+,.0f})")
    return base

def main():
    report = ["INFORME - Evaluacion QualityScorer / HMM (NQ, YM, GC)",
              "Funciones reales del pipeline reaplicadas a los trades del backtest LuxAlgo.",
              "Precio corregido por back-adjustment (delta por roll). Filtros opt-in; score bloquea solo entries; regimen 'unknown' = fail-open."]
    print("\n".join(report), flush=True)
    for sym, cfg in INSTR.items():
        block=[]
        evaluate(sym, cfg, block)
        print("\n".join(block), flush=True)
        report.extend(block)
    open("/tmp/eval_quality_report.txt","w",encoding="utf-8").write("\n".join(report))

if __name__ == "__main__":
    main()
