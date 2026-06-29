import bisect, csv, glob, statistics as st, sys
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo
sys.path.insert(0,".")
from app.services.quality_scorer import _SUBSCORES
from app.services.hmm_service import classify_regime
NY=ZoneInfo("America/New_York"); HOLC="NINJATRADER/HOLC"; LIST="ListaDeOperaciones"
SUBS=("volume_relative","atr_normalized","vwap_position","time_of_day")
TP=6.0
CFG={
 "ES": dict(tok="CME_MINI_ES1!",tf="5m",sl=2.5,lv=[0.75,1.25],q=[0,1,4],win=(560,945),flt=None),
 "NQ": dict(tok="CME_MINI_NQ1!",tf="5m",sl=8.0,lv=[4,5],q=[0,2,2],win=None,flt=None),
 "YM": dict(tok="CBOT_MINI_YM1!",tf="15m",sl=8.0,lv=[1.5,2],q=[0,0,4],win=None,flt="ym"),
 "GC": dict(tok="COMEX_GC1!",tf="5m",sl=2.5,lv=[0.5,0.75],q=[0,0,3],win=(570,945),flt="gc"),
 "RTY":dict(tok="CME_MINI_RTY1!",tf="15m",sl=4.0,lv=[0.5,1.5],q=[3,0,0],win=(570,720),flt=None),
 "6E": dict(tok="CME_6E1!",tf="5m",sl=2.0,lv=[0.5,0.75],q=[3,0,0],win=(570,945),flt=None),
 "6J": dict(tok="CME_6J1!",tf="5m",sl=8.0,lv=[2,3],q=[0,3,0],win=None,flt=None),
 "CL": dict(tok="NYMEX_CL1!",tf="15m",sl=8.0,lv=[0.5,2.5],q=[0,0,3],win=None,flt=None),
}
def _key(s):  # "YYYY-MM-DD HH:MM:SS" -> int YYYYMMDDHHMM
    return int(s[0:4]+s[5:7]+s[8:10]+s[11:13]+s[14:16])
def _kdt(t):  # datetime -> misma clave
    return t.year*100000000+t.month*1000000+t.day*10000+t.hour*100+t.minute
def load(sym,tf):
    H=[];L=[];C=[];V=[];D=[]
    for r in csv.reader(open(f"{HOLC}/{sym}_{tf}.csv",encoding="utf-8-sig")):
        if not r or r[0][0:4].isdigit()==False or not r[0].strip(): continue
        try:
            D.append(_key(r[0]))
            H.append(float(r[2]));L.append(float(r[3]));C.append(float(r[4]));V.append(float(r[5]))
        except: pass
    return None,H,L,C,V,D
def atr_series(H,L,C,p=14):
    n=len(C); trs=[0.0]*n
    trs[0]=H[0]-L[0]
    for i in range(1,n): trs[i]=max(H[i]-L[i],abs(H[i]-C[i-1]),abs(L[i]-C[i-1]))
    atr=[None]*n
    if n<p: return atr
    a=sum(trs[:p])/p; atr[p-1]=a
    for i in range(p,n): a=(a*(p-1)+trs[i])/p; atr[i]=a
    return atr
def load_trades(tok):
    path=glob.glob(f"{LIST}/*{tok}*.csv")[0]; by={}
    for r in csv.reader(open(path,encoding="utf-8-sig")):
        if not r or r[0].strip().lower().startswith("trade"): continue
        by.setdefault(r[0].strip(),[]).append(r)
    T=[]
    for rs in by.values():
        ent=next((x for x in rs if x[1].strip().lower().startswith("entrada")),None)
        ex =next((x for x in rs if x[1].strip().lower().startswith("salida")),None)
        if not ent or not ex: continue
        try:
            T.append(dict(dir="buy" if "largo" in ent[1].lower() else "sell",
                ts=datetime.strptime(ent[2].strip(),"%Y-%m-%d %H:%M"),
                ep=float(ent[4]),xp=float(ex[4]),pnl=float(ent[7]),
                mae=abs(float(ent[11])),mfe=float(ent[9])))
        except: continue
    T.sort(key=lambda t:t["ts"]); return T
def dpp(T):
    v=[]
    for t in T:
        mv=(t["xp"]-t["ep"]) if t["dir"]=="buy" else (t["ep"]-t["xp"])
        if abs(mv)>1e-12: v.append(t["pnl"]/mv)
    return st.median(v) if v else 1.0
def metrics(P):
    pn=[p for p,_ in P]
    if not pn: return dict(n=0,avgW=0,avgL=0,pf=0,net=0,dd=0,worst=0)
    W=[p for p in pn if p>0];Lo=[p for p in pn if p<0]
    g=sum(W);l=-sum(Lo); pf=(g/l) if l>0 else float("inf")
    eq=peak=dd=0.0
    for p,_ in P: eq+=p;peak=max(peak,eq);dd=min(dd,eq-peak)
    return dict(n=len(pn),avgW=(st.mean(W) if W else 0),avgL=(st.mean(Lo) if Lo else 0),pf=pf,net=sum(pn),dd=dd,worst=min(pn))

rows_tbl=[]; diag=[]
for code,c in CFG.items():
    O,H,L,C,V,D=load(code,c["tf"]); atr=atr_series(H,L,C); dn=dpp(load_trades(c["tok"]))
    T=load_trades(c["tok"])
    if c["flt"]=="ym": yO,yH,yL,yC,yV,yD=load(code,"1h")
    cfgq={"timezone":"America/New_York"}
    A=[];B=[]
    for t in T:
        B.append((t["pnl"]*0.2,t["ts"]))
        if c["win"]:
            m=t["ts"].hour*60+t["ts"].minute
            if not (c["win"][0]<=m<=c["win"][1]): continue
        if c["flt"]=="gc":
            i=bisect.bisect_right(D,_kdt(t["ts"]))-1
            if i<21: continue
            sl_=slice(max(0,i-99),i+1)
            dc=C[i]-t["ep"]
            win=[dict(high=H[j],low=L[j],close=C[j],volume=V[j]) for j in range(sl_.start,sl_.stop)]
            sig=SimpleNamespace(price=t["ep"]+dc,action=t["dir"],signal_ts=t["ts"].replace(tzinfo=NY),timeframe="5m")
            sc=round(sum(_SUBSCORES[n](sig,win,cfgq) for n in SUBS)/4*100)
            if sc<55: continue
        elif c["flt"]=="ym":
            j=bisect.bisect_right(yD,_kdt(t["ts"]))-1
            if classify_regime(yC[:j+1])!="ranging": continue
        i=bisect.bisect_right(D,_kdt(t["ts"]))-1
        av=atr[i] if 0<=i<len(atr) else None
        if not av: continue
        atrD=av*dn
        if atrD<=0: continue
        mae_a=t["mae"]/atrD; mfe_a=t["mfe"]/atrD
        sl_hit=mae_a>=c["sl"]; tp_hit=(not sl_hit) and (mfe_a>=TP)
        legpnl=0.0; filled=False
        for k,q in enumerate(c["q"]):
            if q<=0: continue
            lvl=0.0 if k==0 else c["lv"][k-1]
            if mae_a<lvl: continue
            filled=True
            pl=-(c["sl"]-lvl)*atrD if sl_hit else ((TP+lvl)*atrD if tp_hit else t["pnl"]+lvl*atrD)
            legpnl+=q*pl
        if filled: A.append((legpnl/10.0,t["ts"]))
    mA=metrics(A); mB=metrics(B)
    am=[atr[i] for i in range(len(atr)) if atr[i]]
    diag.append(f"  {code}: $/pt≈{dn:.0f} ATR≈{st.median(am):.4g} | A n={mA['n']} B n={mB['n']}")
    for tag,m in (("A",mA),("B",mB)):
        pf="inf" if m["pf"]==float("inf") else f"{m['pf']:.2f}"
        rows_tbl.append(f"{code:4} {tag} {m['n']:>3} {m['avgW']:>8.0f} {m['avgL']:>9.0f} {pf:>5} {m['net']:>9.0f} {m['dd']:>9.0f} {m['worst']:>8.0f}")
    print(diag[-1],flush=True)
hdr=f"{'Estr':4} V {'n':>3} {'avgW$':>8} {'avgL$':>9} {'PF':>5} {'neto$':>9} {'DD$':>9} {'peor$':>8}"
out="A=NTEXECG hoy  B=LuxAlgo 2 micros (nativo)\n"+hdr+"\n"+"\n".join(rows_tbl)
print("\n"+out); open("/tmp/cmpAB_out.txt","w").write("\n".join(diag)+"\n\n"+out)
