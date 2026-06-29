#!/usr/bin/env python3
"""eval_strategy_battery — batería OFICIAL de pruebas sobre una estrategia (SOLO LECTURA).

Sobre una lista de operaciones (trades_*.csv con MAE/MFE) + barras HOLC locales, corre TODAS
las pruebas de calibración de NTEXECG y emite un informe + recomendación. No toca DB.

Pruebas:
  1. Baseline nativo (PF/WR/net/DD/peor)
  2. Barrido SL k×ATR (2/2.5/3/4/8, sin TP)
  3. SL catastrófico = p95 del MAE (cubre crash sin costar neto)
  4. Compras escalonadas (diseños a igual tamaño: base / 1m+1@p50 / 0,1,1@p40,p70)
  5. QualityScorer (score_minimum 55/60/65)
  6. Filtro de régimen HMM 1h (por régimen / solo ranging / bloquear trending_bear)
Todo en USD de microcontrato (×0.2 = 2 micros). PF es independiente del tamaño.

Uso:
  python -m scripts.eval_strategy_battery --trades ruta/trades_NQ_xxx.csv --sym NQ --tf 5m
"""
from __future__ import annotations
import argparse, csv, bisect, statistics as st, sys, os
from types import SimpleNamespace
from datetime import datetime
from zoneinfo import ZoneInfo
sys.path.insert(0,"/home/cadmin/ntexecg") if os.path.isdir("/home/cadmin/ntexecg") else sys.path.insert(0,".")
from app.services.quality_scorer import _SUBSCORES
NY=ZoneInfo("America/New_York"); HOLC="NINJATRADER/HOLC"; SUBS=("volume_relative","atr_normalized","vwap_position","time_of_day")
def kdt(s): return int(s[0:4]+s[5:7]+s[8:10]+s[11:13]+s[14:16])
def regime(closes,lb=30,thr=0.30):
    if len(closes)<20: return "unknown"
    n=min(lb,len(closes)-1); w=closes[-(n+1):]; net=w[-1]-w[0]; path=sum(abs(w[i]-w[i-1]) for i in range(1,len(w)))
    if path<=0: return "ranging"
    return "ranging" if abs(net)/path<thr else ("trending_bull" if net>0 else "trending_bear")
def loadh(sym,tf):
    O=[];H=[];L=[];C=[];V=[];D=[]
    for r in csv.reader(open(f"{HOLC}/{sym}_{tf}.csv",encoding="utf-8-sig")):
        if not r or not r[0][:4].isdigit(): continue
        try: D.append(kdt(r[0]));O.append(float(r[1]));H.append(float(r[2]));L.append(float(r[3]));C.append(float(r[4]));V.append(float(r[5]))
        except: pass
    return O,H,L,C,V,D
def atrser(H,L,C,p=14):
    n=len(C);t=[0.0]*n
    if n: t[0]=H[0]-L[0]
    for i in range(1,n): t[i]=max(H[i]-L[i],abs(H[i]-C[i-1]),abs(L[i]-C[i-1]))
    a=[None]*n
    if n>=p:
        x=sum(t[:p])/p; a[p-1]=x
        for i in range(p,n): x=(x*(p-1)+t[i])/p; a[i]=x
    return a
def load(fn):
    T=[]
    for r in csv.DictReader(open(fn,encoding="utf-8-sig")):
        try: T.append(dict(side=r["side"].strip(),ts=r["entry_time"].strip(),ep=float(r["entry_price"]),xp=float(r["exit_price"]),pnl=float(r["pnl_usd"]),mae=abs(float(r["mae_usd"])),mfe=float(r["mfe_usd"])))
        except: pass
    T.sort(key=lambda t:t["ts"]); return T
def M(P):
    if not P: return dict(n=0,wr=0,pf=0,net=0,dd=0,worst=0)
    W=[p for p in P if p>0];Lo=[p for p in P if p<0];l=-sum(Lo)
    eq=pk=dd=0.0
    for p in P: eq+=p;pk=max(pk,eq);dd=min(dd,eq-pk)
    return dict(n=len(P),wr=100*len(W)/len(P),pf=(sum(W)/l if l>0 else 9.99),net=sum(P),dd=dd,worst=min(P))
def line(t,m): print(f"  {t:22} n={m['n']:>3} WR={m['wr']:5.1f}% PF={m['pf']:5.2f} net=${m['net']:>9,.0f} DD=${m['dd']:>8,.0f} peor=${m['worst']:>8,.0f}")
def pct(v,q): v=sorted(v); return v[min(len(v)-1,int(q*len(v)))]

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--trades",required=True); ap.add_argument("--sym",required=True); ap.add_argument("--tf",default="5m")
    a=ap.parse_args()
    T=load(a.trades)
    O,H,L,C,V,D=loadh(a.sym,a.tf); atr=atrser(H,L,C)
    try: o1,h1,l1,c1,v1,D1=loadh(a.sym,"1h")
    except Exception: c1=D1=None
    dn=st.median([t["pnl"]/((t["xp"]-t["ep"]) if t["side"]=="long" else (t["ep"]-t["xp"])) for t in T if abs(t["xp"]-t["ep"])>1e-12])
    atrs=[]
    for t in T:
        i=bisect.bisect_right(D,kdt(t["ts"]))-1; av=atr[i] if 0<=i<len(atr) and atr[i] else None
        atrs.append(av*dn if av else None)
    med=st.median([x for x in atrs if x]); atrs=[x or med for x in atrs]
    print(f"=== BATERÍA — {os.path.basename(a.trades)} ({a.sym} {a.tf}) · $/pt≈{dn:.4g} · ATR$≈{med:.1f} · USD/micro (2 micros) ===\n")
    print("1) BASELINE nativo"); line("nativo", M([t["pnl"]*0.2 for t in T]))
    print("\n2) BARRIDO SL k×ATR (sin TP)")
    for k in (2,2.5,3,4,8):
        line(f"SL {k:g}×", M([((-k*ad) if (t["mae"]/ad)>=k else t["pnl"])*0.2 for t,ad in zip(T,atrs)]))
    mae_atr=[t["mae"]/ad for t,ad in zip(T,atrs)]; kcat=pct(mae_atr,0.95)
    print(f"\n3) SL CATASTRÓFICO = p95(MAE) = {kcat:.1f}×ATR (≈ ${kcat*med:,.0f}/micro... ${kcat*med*0.2:,.0f} a 2 micros)")
    line(f"SL {kcat:.1f}×", M([((-kcat*ad) if (t["mae"]/ad)>=kcat else t["pnl"])*0.2 for t,ad in zip(T,atrs)]))
    print("\n4) ESCALONADO (igual tamaño 2 micros)")
    p40,p50,p70=pct(mae_atr,.4),pct(mae_atr,.5),pct(mae_atr,.7)
    designs={"base 2@mkt":[(0,2)],"1mkt+1@p50":[(0,1),(p50,1)],"0,1,1@p40,p70":[(p40,1),(p70,1)]}
    for nm,legs in designs.items():
        A=[]
        for t,ad in zip(T,atrs):
            r=0;f=False;ma=t["mae"]/ad
            for lvl,q in legs:
                if ma<lvl: continue
                f=True; r+=q*(t["pnl"]+lvl*ad)/10.0
            if f: A.append(r)
        line(nm,M(A))
    if c1 is not None:
        for t in T:
            i=bisect.bisect_right(D,kdt(t["ts"]))-1
            if i<21: t["sc"]=100; t["rg"]="unknown"; continue
            dc=C[i]-t["ep"]; wv=[dict(high=H[j],low=L[j],close=C[j],volume=V[j]) for j in range(max(0,i-99),i+1)]
            sg=SimpleNamespace(price=t["ep"]+dc,action=("buy" if t["side"]=="long" else "sell"),signal_ts=datetime.strptime(t["ts"],"%Y-%m-%d %H:%M").replace(tzinfo=NY),timeframe=a.tf)
            t["sc"]=round(sum(_SUBSCORES[n](sg,wv,{"timezone":"America/New_York"}) for n in SUBS)/4*100)
            i1=bisect.bisect_right(D1,kdt(t["ts"]))-1; t["rg"]=regime(c1[:i1+1])
        print("\n5) QUALITYSCORER (score_minimum)")
        for thr in (55,60,65): line(f"score≥{thr}", M([t["pnl"]*0.2 for t in T if t.get("sc",100)>=thr]))
        print("\n6) RÉGIMEN HMM 1h")
        reg={}
        for t in T: reg.setdefault(t.get("rg","unknown"),[]).append(t["pnl"]*0.2)
        for r,v in sorted(reg.items()): line(r,M(v))
        line("solo ranging", M([t["pnl"]*0.2 for t in T if t.get("rg")=="ranging"]))
        line("bloquear bear", M([t["pnl"]*0.2 for t in T if t.get("rg")!="trending_bear"]))
    print("\nNota: A vs B completo con scripts.compare_ntexecg_vs_luxalgo. Decisión: ver Anexo 24 §3-§6.")
if __name__=="__main__": asyncio.run if False else main()
