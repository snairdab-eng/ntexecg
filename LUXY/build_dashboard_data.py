"""Genera dashboard data por instrumento: deriva la config de los propios datos
(SL/TP/escalonado por percentiles, BE si ayuda, dirección la que mejore el neto),
todo DENTRO DE MUESTRA con el 100% de los trades. Sin OOS."""
import estudio_estrategia as E, numpy as np, json, os

INSTRUMENTS = [
    ("ES",  "ES ConfNormal", "trades/1783518563576_ES5m_ConfNormal_TC_TSR_070726.csv", "ohlc/ES_5m.csv",  "ohlc/ES_1h.csv"),
    ("NQ",  "NQ ConfAny",    "trades/1783530483331_NQ5m_ConfAny_ST_TC_070726.csv",     "ohlc/NQ_5m.csv",  "ohlc/NQ_1h.csv"),
    ("RTY", "RTY ConfNormal","trades/1783530483332_RTY15m_ConfNormal_NC_TST_070726.csv","ohlc/RTY_15m.csv","ohlc/RTY_1h.csv"),
    ("6E",  "6E ConfStrong", "trades/1783530483333_6E5m_ConfStrong_NC_WeakConf_070726.csv","ohlc/6E_5m.csv","ohlc/6E_1h.csv"),
    ("6J",  "6J ConfNormal", "trades/1783530483334_6J5m_ConfNormal_TSR_MF50_070726.csv","ohlc/6J_5m.csv","ohlc/6J_1h.csv"),
    ("GC",  "GC ContraNormal","trades/1783530483335_GC5m_ContraNormal_ST_WeakConf_070726.csv","ohlc/GC_5m.csv","ohlc/GC_1h.csv"),
]
NEWS = [8, 11]
ZONES = [('Asia',[19,20,21,22,23,0,1]), ('Europa/Londres',[2,3,4,5,6,7]),
         ('Apertura US',[8,9]), ('NY media',[10,11]),
         ('NY tarde',[12,13,14,15]), ('Cierre US',[16,17,18])]

def alloc_from(w, total=10):
    """Reparte 'total' micros proporcional a los pesos w, redondeo por mayor residuo, C1>=1."""
    s=sum(w) or 1.0
    raw=[wi/s*total for wi in w]
    base=[int(x) for x in raw]
    rem=total-sum(base)
    order=sorted(range(len(raw)), key=lambda i: raw[i]-base[i], reverse=True)
    for i in range(rem): base[order[i]]+=1
    if base[0]==0: base[0]=1; base[max(range(1,len(base)),key=lambda i:base[i])]-=1
    return base

def M(p):
    p = np.asarray(p, float)
    if len(p) == 0: return dict(net=0,pf=0,dd=0,worst=0,wr=0,n=0)
    gp=p[p>0].sum(); gl=p[p<0].sum(); eq=p.cumsum(); dd=float((eq-np.maximum.accumulate(eq)).min())
    return dict(net=float(p.sum()), pf=float(gp/abs(gl)) if gl!=0 else 999.0,
                dd=dd, worst=float(p.min()), wr=float((p>0).mean()*100), n=int(len(p)))

def why_alloc(alloc, f2, f3):
    a=alloc; p2,p3=round(f2*100),round(f3*100)
    if a[1]+a[2]==0:
        prof="prácticamente no hace pullbacks"; tail="agregar por debajo casi nunca se ejecutaría, así que casi todo el tamaño entra en la señal (C1)."
    elif a[2]==0:
        prof="casi no hace pullbacks profundos"; tail="C3 rara vez se llenaría, por eso queda en 0 y el peso se concentra en C1–C2."
    elif a[0]>a[1]+a[2]:
        prof="hace pullbacks moderados"; tail="la mayor parte del tamaño entra arriba y se agrega con mesura en C2/C3."
    else:
        prof="hace pullbacks frecuentes y profundos"; tail="conviene dejar bastante tamaño para C2 y C3, donde el precio sí suele darte fill."
    return (f"El precio baja hasta C2 en el {p2}% de las operaciones y hasta C3 en el {p3}%. "
            f"Como esta estrategia {prof}, el estudio reparte {a[0]}/{a[1]}/{a[2]}: {tail}")

def build(inst, name, tf, o5f, o1f):
    E.CONFIG.update({'trade_file':tf,'ohlc_5m_file':o5f,'ohlc_1h_file':o1f,'point_value':'auto'})
    t = E.load_trades(E.CONFIG); PV = E.PV
    MES = PV / 10.0   # valor del micro = mini/10 (MES=5, MNQ=2, MGC=10, ...)
    o5 = E.load_ohlc(o5f, E.CONFIG); paths, ok = E.reconstruct_paths(t, o5)
    mae = t.mae_usd.values; mfe = t.mfe_usd.values; raw = t.pnl.values.astype(float)
    hr = t.entry_dt.dt.hour.values; dow = t.entry_dt.dt.dayofweek.values; longs = t.is_long.values
    absmae = np.abs(mae)
    # --- desglose por día de la semana ---
    DAYN={0:'Lunes',1:'Martes',2:'Miércoles',3:'Jueves',4:'Viernes',5:'Sábado',6:'Domingo'}
    daystats=[]
    for dd in sorted(set(int(x) for x in dow)):
        dm=dow==dd; mm=M(raw[dm])
        daystats.append({'dow':dd,'name':DAYN[dd],'n':int(dm.sum()),
                         'net':round(mm['net']),'wr':round(mm['wr'],1),'pf':round(mm['pf'],2),
                         'losing':bool(mm['net']<0 and dm.sum()>=8),'blocked':False})
    # --- desglose por sesión / zona de bolsa; bloquea por defecto las de neto negativo ---
    zstats=[]
    for zname,zhrs in ZONES:
        zm=np.isin(hr,zhrs); mm=M(raw[zm])
        zstats.append({'name':zname,'hours':zhrs,'n':int(zm.sum()),
                       'net':round(mm['net']), 'wr':round(mm['wr'],1),
                       'pf':round(mm['pf'],2),
                       'losing': bool(mm['net']<0 and zm.sum()>=8),  # candidata a bloquear
                       'blocked': False})                            # por defecto no se bloquea nada
    blocked_hours=sorted({h for z in zstats if z['blocked'] for h in z['hours']})
    noBlock = ~np.isin(hr, blocked_hours)

    # --- niveles derivados (USD -> puntos) ---
    SLcand = sorted(set(np.round(np.percentile(absmae,[60,75,85,95]),-1)))
    TPusd  = float(np.round(1.1*mfe.max(),-1))
    # niveles de escalonado RELATIVOS al tamaño típico del movimiento a favor
    med_mfe = float(np.median(mfe[mfe>0])) if (mfe>0).any() else float(np.median(absmae)+1)
    l2usd = float(np.round(0.5*med_mfe,-1)); l3usd = float(np.round(1.0*med_mfe,-1))
    if l3usd<=l2usd: l3usd=l2usd*1.8
    # frecuencia real con que el pullback llega a cada nivel -> reparto de micros
    f2 = float((absmae>=l2usd).mean()); f3 = float((absmae>=l3usd).mean())
    ALLOC = alloc_from([1.0, f2, f3], 10)   # micros en C1/C2/C3, derivado del estudio
    BEcand = sorted(set(np.round(np.percentile(mfe[mfe>0],[20,35,50]),-1)))

    def tpnl(i, SLu, BEu, TPu, alloc):
        f=paths['FAV'][i]; a=paths['ADV'][i]
        if f is None: return raw[i]
        SLp,BEp,TPp = SLu/PV, (BEu/PV if BEu else None), TPu/PV
        armed=False; Ex=None; xb=len(f)-1
        for k in range(len(f)):
            if a[k]<=-SLp: Ex=-SLp; xb=k; break
            if BEp is not None and armed and a[k]<=0: Ex=0.0; xb=k; break
            if f[k]>=TPp: Ex=TPp; xb=k; break
            if BEp is not None and f[k]>=BEp: armed=True
        if Ex is None: Ex=t.pnl_pts.iloc[i]
        tot=0.0
        for Lu,q in zip([0,l2usd,l3usd], alloc):
            if q==0: continue
            Lp=Lu/PV
            if (Lp==0) or any(a[k]<=-Lp for k in range(xb+1)): tot+=q*(Ex+Lp)*MES
        return tot

    def cfg_pnl(SLu,BEu,TPu,alloc): return np.array([tpnl(i,SLu,BEu,TPu,alloc) for i in range(len(t))])
    ONE=[10,0,0]   # 1 mini en la entrada (para elegir SL/BE de forma robusta)

    def score(m): return m['net']/abs(m['dd']) if m['dd']!=0 else m['net']
    SLu = max(SLcand, key=lambda s: score(M(cfg_pnl(s,None,TPusd,ONE))))
    base_noBE = M(cfg_pnl(SLu,None,TPusd,ONE))['net']
    BEbest = max(BEcand, key=lambda b: M(cfg_pnl(SLu,b,TPusd,ONE))['net']) if BEcand else None
    BEu = BEbest if (BEbest and M(cfg_pnl(SLu,BEbest,TPusd,ONE))['net']>base_noBE) else None
    cfg = cfg_pnl(SLu,BEu,TPusd,ALLOC)   # config final con el reparto derivado
    scalein = (ALLOC[1]+ALLOC[2])>0
    # dirección: ambos / largos / cortos por mejor neto (con las zonas bloqueadas)
    dirs = {'both':noBlock, 'long':noBlock&longs, 'short':noBlock&~longs}
    dirchoice = max(dirs, key=lambda d: M(cfg[dirs[d]])['net'])
    taken = dirs[dirchoice]

    base = M(raw); base['part']=100.0
    conf = M(cfg[taken]); conf['part']=float(taken.mean()*100)

    # --- régimen (EMA50 1h) como contexto ---
    regime=None
    try:
        o1=E.load_ohlc(o1f,E.CONFIG); w=o1.loc[t.entry_dt.min():t.exit_dt.max()]
        if len(w)>=10:
            ema=w['c'].ewm(span=50).mean(); chg=float((w['c'].iloc[-1]/w['c'].iloc[0]-1)*100); pu=float((w['c']>ema).mean()*100)
            regime={'change':round(chg,1),'pct_up':round(pu),
                    'label':'alcista' if pu>55 else ('bajista' if pu<45 else 'lateral')}
    except Exception: regime=None

    # --- time-stop: diagnóstico por duración (se descarta por sesgo de supervivencia) ---
    dur=t.dur.values
    tsb=[]
    for lab,lo,hi in [('0–30',0,30),('30–80',30,80),('80–160',80,160),('160+',160,10**9)]:
        dm=(dur>=lo)&(dur<hi); mm=M(raw[dm]); tsb.append({'range':lab,'n':int(dm.sum()),'net':round(mm['net'])})
    timestop={'buckets':tsb,'verdict':'descartado',
        'why':("Tentador: las operaciones largas pierden (neto negativo más allá de ~80 barras), "
               "así que cortarlas por tiempo <i>parece</i> ayudar. Pero el estudio validó intrabar que "
               "muchas de esas operaciones se hunden y luego se recuperan — cortarlas por tiempo quita "
               "esas recuperaciones. Es <b>sesgo de supervivencia</b>, por eso el time-stop se <b>descarta</b>. "
               "Aplicarlo requeriría re-validar intrabar en cada estrategia.")}

    trades=[{'i':i+1,'mfe':round(float(mfe[i]),2),'mae':round(float(mae[i]),2),
             'pnl':round(float(raw[i]),2),'long':bool(longs[i]),'hr':int(hr[i]),'dow':int(dow[i])} for i in range(len(t))]
    reco={'sl_usd':round(SLu),'tp_usd':round(TPusd),'be_usd':(round(BEu) if BEu else None),
          'l2_usd':round(l2usd),'l3_usd':round(l3usd),'alloc':[int(x) for x in ALLOC],
          'fill2':round(f2*100),'fill3':round(f3*100),
          'sl_pts':round(SLu/PV,1),'tp_pts':round(TPusd/PV,1),'be_pts':(round(BEu/PV,1) if BEu else None),
          'l2_pts':round(l2usd/PV,1),'l3_pts':round(l3usd/PV,1),
          'scalein':bool(scalein),'dir':dirchoice,'news_hours':NEWS,
          'zones':zstats,'blocked_hours':blocked_hours,'days':daystats,
          'why_alloc':why_alloc(ALLOC,f2,f3)}
    # bandera de fragilidad
    frac=ok/len(t); notes=[]
    if frac<0.90: notes.append(f"reconstrucción {ok}/{len(t)} ({frac*100:.0f}%)")
    if base['net']<0 and conf['net']>0: notes.append("la config voltea un sistema perdedor")
    if base['net']>0 and conf['net']>3*base['net']: notes.append("mejora >3× (depende del escalonado en pullbacks)")
    fragile=len(notes)>0
    return dict(inst=inst,name=name,pv=PV,n=len(t),recon_ok=int(ok),
                fragile=fragile,notes=notes,regime=regime,timestop=timestop,
                mfe_max=round(float(mfe.max()),2),mae_min=round(float(mae.min()),2),
                trades=trades,base=base,config=conf,reco=reco)

if __name__=='__main__':
    ALL={}; ORDER=[]
    for inst,name,tf,o5f,o1f in INSTRUMENTS:
        d=build(inst,name,tf,o5f,o1f); ALL[inst]=d; ORDER.append(inst)
        r=d['reco']; flag=' ⚠ '+'; '.join(d['notes']) if d['fragile'] else ''
        print(f"\n=== {inst} ({name}) · recon {d['recon_ok']}/{d['n']}{flag} ===")
        print(f"  reparto {r['alloc'][0]}/{r['alloc'][1]}/{r['alloc'][2]} · {r['why_alloc']}")
    json.dump({'order':ORDER,'instruments':ALL}, open('out/dash_all.json','w'))
    print("\n>> out/dash_all.json escrito con",len(ORDER),"instrumentos")
