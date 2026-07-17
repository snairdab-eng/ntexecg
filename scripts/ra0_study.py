#!/usr/bin/env python3
"""RA-0 — Estudio de inferencia para el re-armado de piernas (SOLO análisis).

Base: CONTRATO/SPEC_Rearmado_Piernas_2026-07-15.md §5 (R-RA1..9). Cero despacho.
Con el intrabar del HOLC ALINEADO (walk B4.0/touch_minutes a TODA la vida del
trade), calibra por activo: curva de llegada (R-RA4), R-RA3 graduada, PnL
marginal de fills tardíos (¿suman o cuchillos?), orden de eventos backstop/TP vs
C2/C3 (R-RA6), sensibilidad de ventana ciega (§1), y ATR-expansión (R-RA7).

REUSA el intrabar sancionado (`lab_analyze.touch_minutes`, entrada→salida) y el
motor de fills (`mr_sims.ladder_outcome`/`leg_filled` con corte por minutos). Las
funciones estadísticas son PURAS y testeadas (tests/test_ra0_study.py).
"""
from __future__ import annotations

import statistics as _stats


# ---------------------------------------------------------------------------
# Helpers PUROS (testeados) — estadística sobre listas de minutos/features
# ---------------------------------------------------------------------------

def pctl(vals: list[float], p: float) -> float | None:
    """Percentil lineal (mismo estimador que el resto del motor)."""
    s = sorted(v for v in vals if v is not None)
    if not s:
        return None
    i = (len(s) - 1) * p
    lo, hi = int(i), min(int(i) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (i - lo), 1)


def arrival_stats(mins: list) -> dict:
    """Curva de llegada de UNA profundidad: `mins` = minuto del PRIMER toque por
    trade (None = nunca tocó en la vida del trade). Mediana/p90/p95 de los que
    tocaron + % acumulado que llega ≤1h / ≤2h / ≤3h y % que NUNCA."""
    n = len(mins)
    touched = [m for m in mins if m is not None]
    nt = len(touched)

    def cum(lim):
        return round(100.0 * sum(1 for m in touched if m <= lim) / n, 1) if n else 0.0

    return {
        "n": n, "n_touched": nt,
        "touch_rate_pct": round(100.0 * nt / n, 1) if n else 0.0,
        "mediana_min": pctl(touched, 0.5), "p90_min": pctl(touched, 0.9),
        "p95_min": pctl(touched, 0.95),
        "pct_le_1h": cum(60), "pct_le_2h": cum(120), "pct_le_3h": cum(180),
        "pct_nunca": round(100.0 * (n - nt) / n, 1) if n else 0.0,
    }


def blind_window_pct(mins: list, cycle_min: float = 62.0,
                     live_min: float = 60.0) -> dict:
    """Sensibilidad de ventana ciega (§1): ciclos de `cycle_min`, la orden vive
    `live_min` y se re-arma al minuto live→cycle. Un toque en la franja
    [live, cycle) de su ciclo cae en ventana ciega (fill perdido honesto)."""
    touched = [m for m in mins if m is not None]
    if not touched:
        return {"n_touched": 0, "pct_en_ciega": 0.0}
    ciegos = sum(1 for m in touched if live_min <= (m % cycle_min) < cycle_min)
    return {"n_touched": len(touched),
            "pct_en_ciega": round(100.0 * ciegos / len(touched), 1)}


def graduated_prob(feats: list, depth_key: str, t_min: float, k: float) -> dict:
    """R-RA3 graduada: P(toca C2 en (t,fin] | sin toque hasta t Y el precio en t
    está ≥ k×ATR del lado FAVORABLE de C0). `feats` por trade con:
      · `depth_key` → minuto de toque de esa profundidad (None si nunca),
      · `fav_at[t_min]` → excursión favorable en ×ATR en el minuto t.
    Denominador = trades que en t NO habían tocado y estaban ≥k favorable;
    numerador = de esos, los que tocan DESPUÉS de t."""
    cond = []
    for f in feats:
        tm = f.get(depth_key)
        fav = (f.get("fav_at") or {}).get(t_min)
        if fav is None:
            continue
        no_touch_yet = tm is None or tm > t_min
        if no_touch_yet and fav >= k:
            cond.append(tm is not None and tm > t_min)   # toca luego
    n = len(cond)
    return {"n_cond": n,
            "p_toque_luego_pct": round(100.0 * sum(cond) / n, 1) if n else None}


def order_of_events(feats: list) -> dict:
    """R-RA6 — % de trades donde el BACKSTOP o el TP se toca ANTES que C2/C3
    (piernas que nacerían HUÉRFANAS sin la regla de muerte inferida)."""
    n = len(feats)
    huerf_c2 = huerf_c3 = 0
    for f in feats:
        death = _first([f.get("bk_min"), f.get("tp_min")])
        for leg, key in (("c2", "huerf_c2"), ("c3", "huerf_c3")):
            lm = f.get(f"{leg}_min")
            if lm is not None and death is not None and death < lm:
                if leg == "c2":
                    huerf_c2 += 1
                else:
                    huerf_c3 += 1
    return {"n": n,
            "pct_c2_huerfana": round(100.0 * huerf_c2 / n, 1) if n else 0.0,
            "pct_c3_huerfana": round(100.0 * huerf_c3 / n, 1) if n else 0.0}


def _first(vals: list):
    xs = [v for v in vals if v is not None]
    return min(xs) if xs else None


def atr_expansion_split(feats: list, depth_key: str = "c2_min",
                        late_after: float = 60.0) -> dict:
    """R-RA7 — en los toques TARDÍOS (>late_after) de una pierna, distribución de
    ATR_t/ATR_señal, separando fills GANADORES vs PERDEDORES (native_pnl)."""
    ratio_key = depth_key.replace("_min", "_atr_ratio")
    win, los = [], []
    for f in feats:
        tm = f.get(depth_key)
        r = f.get(ratio_key)
        if tm is None or tm <= late_after or r is None:
            continue
        (win if f.get("native_pnl", 0.0) > 0 else los).append(r)
    return {
        "n_tardios": len(win) + len(los),
        "ganadores": {"n": len(win), "atr_ratio_med": pctl(win, 0.5),
                      "atr_ratio_p90": pctl(win, 0.9)},
        "perdedores": {"n": len(los), "atr_ratio_med": pctl(los, 0.5),
                       "atr_ratio_p90": pctl(los, 0.9)},
    }


# ---------------------------------------------------------------------------
# Reunión de datos (no puro — usa HOLC + motor). Debajo del fold de helpers.
# ---------------------------------------------------------------------------

def _atr_series(keys, bars, period: int = 14) -> dict:
    """ATR(14) rodante por barra {ts: atr} (mismo TR que _calc_atr, simple)."""
    out: dict = {}
    trs: list[float] = []
    prev_close = None
    for i, ts in enumerate(keys):
        _o, h, lo, c, _v = bars[ts]
        tr = (h - lo) if prev_close is None else max(h - lo, abs(h - prev_close),
                                                     abs(lo - prev_close))
        trs.append(tr)
        prev_close = c
        if i >= period:
            out[ts] = sum(trs[i - period + 1:i + 1]) / period
    return out


def enrich_master(master_csv, holc_csv, activo):
    """Enriquece el master contra su HOLC alineado; devuelve
    (trades_confiables, keys, idx, bars, off, contencion_pct)."""
    from pathlib import Path
    from scripts.lab_analyze import (detect_tz_offset, enrich_with_bars,
                                     load_holc_from_path, mark_no_contenido,
                                     parse_luxalgo_csv)
    from scripts.mr_report import TICK_SIZE
    trades = parse_luxalgo_csv(Path(master_csv))
    bars = load_holc_from_path(holc_csv)
    off, sanity, _ = detect_tz_offset(trades, bars)
    enrich_with_bars(trades, bars, off)
    # LX-13 — excluye outliers de frontera de roll (su intrabar es basura)
    mark_no_contenido(trades, bars, off, TICK_SIZE.get(activo))
    keys = sorted(bars)
    idx = {k: i for i, k in enumerate(keys)}
    conf = [t for t in trades if not getattr(t, "no_contenido", False)]
    cont = round(100.0 * len(conf) / len(trades), 1) if trades else 0.0
    return conf, keys, idx, bars, off, cont


def trade_features(t, keys, idx, bars, c2, c3, tp_by_side, bk_pts, atrser, off):
    """Features por trade para TODAS las tablas: minutos de toque de C2/C3
    (adverso), backstop (adverso, ×ATR = bk_pts/ATR_señal), TP (favorable, ×ATR
    del lado); excursión favorable ×ATR en t=60/120/180; ATR_t/ATR_señal en C2."""
    from datetime import timedelta
    from scripts.lab_analyze import touch_minutes
    f = {"native_pnl": t.pnl_usd, "c2_min": None, "c3_min": None,
         "bk_min": None, "tp_min": None, "fav_at": {}, "c2_atr_ratio": None,
         "c3_atr_ratio": None}
    if t.aligned_ts is None or not t.atr_pct or t.atr_entry is None:
        return f
    tp_atr = (tp_by_side or {}).get(t.side) or 8.0
    bk_atr = round(bk_pts / t.atr_entry, 3) if (bk_pts and t.atr_entry) else None
    adv = tuple(x for x in (c2, c3, bk_atr) if x)
    adv_d, fav_d = touch_minutes(t, keys, idx, bars, adverse_lvls=adv,
                                 favor_lvls=(tp_atr,))
    f["c2_min"] = adv_d.get(str(float(c2)))
    f["c3_min"] = adv_d.get(str(float(c3)))
    if bk_atr:
        f["bk_min"] = adv_d.get(str(float(bk_atr)))
    f["tp_min"] = fav_d.get(str(float(tp_atr)))
    # excursión FAVORABLE ×ATR en el minuto t (precio en t vs C0)
    delta = timedelta(minutes=off)
    i0 = idx[t.aligned_ts] + 1
    ref, den = t.bar_close, t.entry_price
    for tt in (60.0, 120.0, 180.0):
        best = None
        for k5 in keys[i0:]:
            mins = (k5 - t.aligned_ts).total_seconds() / 60.0
            if mins > tt:
                break
            _o, high, low, c, _v = bars[k5]
            fav = ((high - ref) if t.side == "long" else (ref - low)) / den * 100.0
            best = fav / t.atr_pct
        f["fav_at"][tt] = round(best, 3) if best is not None else None
    # ATR_t/ATR_señal en el toque de C2/C3
    for leg, mn in (("c2", f["c2_min"]), ("c3", f["c3_min"])):
        if mn is None:
            continue
        ts_touch = t.aligned_ts + timedelta(minutes=mn)
        # barra más cercana ≤ ts_touch
        atr_t = None
        for k5 in keys[i0:]:
            if k5 > ts_touch:
                break
            atr_t = atrser.get(k5, atr_t)
        if atr_t and t.atr_entry:
            f[f"{leg}_atr_ratio"] = round(atr_t / t.atr_entry, 3)
    return f


def _simtrade(t, feat, c2, c3):
    """SimTrade LOCAL (frozen) con los toques full-life en `pb_touch_min` —
    NO muta el trade del estudio; decoupla la sección de la evaluación principal."""
    from scripts.mr_sims import SimTrade
    pbt = {k: v for k, v in ((str(float(c2)), feat["c2_min"]),
                             (str(float(c3)), feat["c3_min"])) if v is not None}
    return SimTrade(
        number=t.number, side=t.side, in_sample=t.in_sample,
        entry_price=t.entry_price, atr_pts=t.atr_entry,
        mae_pts=t.mae_pct / 100.0 * t.entry_price,
        mfe_pts=t.mfe_pct / 100.0 * t.entry_price,
        native_pnl_usd=t.pnl_usd, atr_estimado=False,
        pb_touch_min=pbt or None)


def _ladder_sts_legs(trades, feats, c2, c3, quantities):
    """(sts, legs) compartidos por los cortes — SimTrades con ATR real (los demás
    fuera del universo, como en el motor) y el vector de piernas normalizado."""
    total = sum(q for q in quantities if q > 0) or 1
    legs = tuple((d, q / total) for d, q in
                 zip((0.0, float(c2), float(c3)), quantities) if q > 0)
    sts = [_simtrade(t, f, c2, c3) for t, f in zip(trades, feats)
           if t.atr_entry and t.atr_entry > 0]
    return sts, legs


def ladder_cuts(trades, feats, c2, c3, quantities, bk_pts, tp_by_side, ppt):
    """La PREGUNTA DE ORO: ladder outcome por política de corte
    (1h/2h/3h/duración). Devuelve {cut: metrics_usd}. `trades` con ATR real
    (los demás quedan fuera del universo, como en el motor)."""
    from scripts.mr_sims import HaircutCfg, ladder_outcome, metrics_usd
    sts, legs = _ladder_sts_legs(trades, feats, c2, c3, quantities)
    hc = HaircutCfg()
    out = {}
    for label, cut in (("1h", 3600.0), ("2h", 7200.0), ("3h", 10800.0),
                       ("duracion", None)):
        pnls = [ladder_outcome(st, legs, bk_pts, tp_by_side, ppt, hc, cut)[0]
                for st in sts]
        out[label] = metrics_usd(pnls)
    return out


def ladder_cut_rearmado(trades, feats, c2, c3, quantities, bk_pts, tp_by_side,
                        ppt, max_ciclos):
    """RA-1 — corte por RE-ARMADO: la pierna se re-arma cada ciclo hasta
    `max_ciclos` (del veredicto RA-0v3), con la ventana ciega descontada dentro de
    `leg_filled`. Columna comparativa entre corte 1h y sin-corte. INFORMATIVA:
    R-T1 — el default del estudio SIGUE siendo corte 1h hasta que RA-2 exista en
    despacho; el re-armado jamás es el default aquí."""
    from scripts.mr_sims import HaircutCfg, ladder_outcome, metrics_usd
    sts, legs = _ladder_sts_legs(trades, feats, c2, c3, quantities)
    hc = HaircutCfg()
    pnls = [ladder_outcome(st, legs, bk_pts, tp_by_side, ppt, hc,
                           None, rearm_ciclos=int(max_ciclos))[0] for st in sts]
    return metrics_usd(pnls)


# muestra mínima por celda (patrón LX-7/14) — bajo esto → "n/s" + default conservador
PIERNAS_N_MIN = 10
# RA-0v3 — la recomendación tiene JUICIO económico: el horizonte sale de la TABLA
# DE ORO (Δnet por corte), no de la curva de llegada ciega (que daba MAX_CICLOS=57
# a GC contra una tabla en rojo). Tolerancias del veredicto y tope duro de ciclos:
PEOR_TOLERANCIA_PCT = 15.0   # cuánto puede empeorar el peor-trade vs. el corte 1h
PF_TOLERANCIA_PCT = 10.0     # cuánta caída RELATIVA de PF vs. el corte 1h se tolera
MAX_CICLOS_CAP = 8           # tope duro (~una jornada de ciclos de 62m); jamás 57

# minuto-horizonte de cada corte de la tabla de oro (None = vida de la posición)
_CORTE_MIN = {"1h": 60.0, "2h": 120.0, "3h": 180.0, "duracion": None}


def _finito(x) -> bool:
    import math
    try:
        return x is not None and math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def _peor_no_degrada(base, peor, tol_pct: float = PEOR_TOLERANCIA_PCT) -> bool:
    """El peor-trade del corte no empeora más de tol% respecto al de 1h. El
    peor-trade es ≤0 (una pérdida); 'empeorar' = hacerse MÁS negativo."""
    if base is None or peor is None:
        return True
    if base >= 0:                      # sin perdedores a 1h → cualquier pérdida degrada
        return peor >= 0
    return peor >= base * (1 + tol_pct / 100.0)   # base<0 → piso más negativo


def _pf_no_degrada(base, pf, tol_pct: float = PF_TOLERANCIA_PCT) -> bool:
    """El PF del corte no cae materialmente (>tol% relativo) respecto al de 1h."""
    if not _finito(base):
        return True                    # 1h sin pérdidas (PF ∞/None) → no bloquea
    if not _finito(pf):
        return True                    # el corte mejoró a sin pérdidas
    return float(pf) >= float(base) * (1 - tol_pct / 100.0)


def veredicto_rearmado(cortes: dict, n_fills_tardios: int) -> dict:
    """RA-0v3 — VEREDICTO económico del re-armado leído de la TABLA DE ORO.
    Mejor horizonte = corte >1h con Δnet acumulado (vs. 1h) MÁXIMO que NO degrada
    el peor-trade más de PEOR_TOLERANCIA_PCT ni el PF materialmente. Si ningún
    corte >1h mejora → NO recomendado. Muestra de fills tardíos chica → n/s
    (default OFF, conservador): sin evidencia no se re-arma."""
    base = cortes.get("1h") or {}
    base_net = base.get("net_usd")
    if (n_fills_tardios or 0) < PIERNAS_N_MIN or base_net is None:
        return {"veredicto": "n/s", "mejor_horizonte": None, "delta_net_usd": None,
                "texto": (f"n/s — sin evidencia para re-armar "
                          f"({n_fills_tardios or 0} fills tardíos < {PIERNAS_N_MIN}); "
                          f"default OFF")}
    best_lbl, best_delta = None, 0.0
    for lbl in ("2h", "3h", "duracion"):
        m = cortes.get(lbl) or {}
        net = m.get("net_usd")
        if net is None:
            continue
        delta = net - base_net
        if delta <= 0:                                   # no suma neto
            continue
        if not _peor_no_degrada(base.get("peor_trade_usd"), m.get("peor_trade_usd")):
            continue                                     # suma, pero a costa del peor-caso
        if not _pf_no_degrada(base.get("pf"), m.get("pf")):
            continue                                     # el PF se cae materialmente
        if delta > best_delta:
            best_lbl, best_delta = lbl, delta
    if best_lbl is None:
        return {"veredicto": "no_recomendado", "mejor_horizonte": None,
                "delta_net_usd": None,
                "texto": "NO recomendado — los fills tardíos restan (ningún corte "
                         ">1h mejora el neto sin degradar peor-trade/PF)"}
    return {"veredicto": "recomendado", "mejor_horizonte": best_lbl,
            "delta_net_usd": round(best_delta, 2),
            "texto": f"re-armado RECOMENDADO (hasta {best_lbl}) — los fills tardíos "
                     f"SUMAN +${best_delta:,.0f} de neto sin degradar el peor-caso"}


def recomendar(section: dict) -> dict:
    """Recomendación por estrategia con EVIDENCIA (constantes PROPUESTAS para
    RA-1/2). Jamás constante sin muestra: celda floja → n/s + default conservador.
      · VEREDICTO + MAX_CICLOS: del JUICIO económico sobre la TABLA DE ORO
        (RA-0v3), no de la curva de llegada ciega. MAX_CICLOS = ⌈mejor_horizonte/
        62⌉ con tope MAX_CICLOS_CAP; NO recomendado / n/s → 1 (OFF).
      · K_SOBRE_C0: el k donde P(toque tardío) cae bajo ~15% a t=1h (R-RA3).
      · UMBRAL_ATR_EXPANSION: ATR_t/ATR_señal p90 de los fills tardíos PERDEDORES
        (por encima → el nivel ya no significa lo mismo, R-RA7); sin muestra → 1.5.
    """
    import math
    vd = veredicto_rearmado(section.get("cortes") or {},
                            section.get("n_fills_tardios", 0))
    # MAX_CICLOS del VEREDICTO (no del p90 ciego): ⌈horizonte/62⌉, tope duro CAP.
    if vd["veredicto"] != "recomendado":
        max_ciclos = 1
        ev_ciclos = ("n/s (sin evidencia de fills tardíos) → default 1 (OFF)"
                     if vd["veredicto"] == "n/s"
                     else "NO recomendado → 1 (OFF, corte 1h)")
    else:
        hz = _CORTE_MIN.get(vd["mejor_horizonte"])
        if hz is None:                     # 'duracion' = sin tope de reloj → cap duro
            max_ciclos = MAX_CICLOS_CAP
            ev_ciclos = (f"mejor horizonte = vida de la posición "
                         f"(Δnet +${vd['delta_net_usd']:,.0f}) → tope {MAX_CICLOS_CAP}")
        else:
            crudo = math.ceil(hz / 62.0)
            max_ciclos = max(1, min(MAX_CICLOS_CAP, crudo))
            ev_ciclos = (f"mejor horizonte = {vd['mejor_horizonte']} "
                         f"(Δnet +${vd['delta_net_usd']:,.0f}) → ⌈{int(hz)}/62⌉"
                         + (f" (tope {MAX_CICLOS_CAP})" if crudo > MAX_CICLOS_CAP else ""))
    # K_SOBRE_C0: menor k con P(toque luego)≤15% y n≥min a t=60
    k_sobre, ev_k = None, "n/s"
    for k in (0.0, 0.5, 1.0):
        cell = (section.get("graduada") or {}).get((60.0, k)) or {}
        p = cell.get("p_toque_luego_pct")
        if cell.get("n_cond", 0) >= PIERNAS_N_MIN and p is not None and p <= 15.0:
            k_sobre, ev_k = k, f"P(toque|k≥{k},t=1h)={p}% (n{cell['n_cond']})"
            break
    if k_sobre is None:
        k_sobre, ev_k = 1.0, "n/s (ninguna celda con muestra baja la prob) → default 1.0"
    ae = (section.get("atr_exp_c3") or {}).get("perdedores") or {}
    if (ae.get("n") or 0) < PIERNAS_N_MIN or ae.get("atr_ratio_p90") is None:
        umbral_atr, ev_atr = 1.5, "n/s (pocos fills tardíos perdedores) → default 1.5"
    else:
        umbral_atr = ae["atr_ratio_p90"]
        ev_atr = f"p90 ATR_t/ATR_señal de fills tardíos perdedores C3 = {umbral_atr}"
    return {
        "veredicto": vd["veredicto"], "veredicto_texto": vd["texto"],
        "mejor_horizonte": vd["mejor_horizonte"], "delta_net_usd": vd["delta_net_usd"],
        "MAX_CICLOS": max_ciclos, "MAX_CICLOS_evidencia": ev_ciclos,
        "K_SOBRE_C0": k_sobre, "K_SOBRE_C0_evidencia": ev_k,
        "UMBRAL_ATR_EXPANSION": umbral_atr, "UMBRAL_ATR_EXPANSION_evidencia": ev_atr,
    }


def piernas_section(trades, keys, idx, bars, off, *, c2, c3, quantities,
                    bk_pts, tp_by_side, ppt) -> dict:
    """Sección 'Piernas / Re-armado' FIJA del estudio (RA-0v2) — determinista del
    master + intrabar YA enriquecido de la clave. `trades` = Lab Trades
    enriquecidos (los no_contenido de LX-13 se EXCLUYEN aquí). C2/C3 = ladder del
    estudio (×ATR). Devuelve el dict que va a dashboard['piernas']."""
    conf = [t for t in trades if not getattr(t, "no_contenido", False)]
    atrser = _atr_series(keys, bars)
    feats = [trade_features(t, keys, idx, bars, c2, c3, tp_by_side, bk_pts,
                            atrser, off) for t in conf]
    section = {
        "c2": c2, "c3": c3, "quantities": list(quantities), "backstop_pts": bk_pts,
        "tp_por_lado_atr": dict(tp_by_side or {}), "n": len(conf),
        "n_min_celda": PIERNAS_N_MIN,
        # RA-0v3 — muestra de la que depende el VEREDICTO: trades con un fill de
        # pierna profunda DESPUÉS de 1h (lo que un horizonte >1h captura de más).
        "n_fills_tardios": sum(1 for f in feats
                               if (f.get("c2_min") or 0) > 60.0
                               or (f.get("c3_min") or 0) > 60.0),
        "arrival_c2": arrival_stats([f["c2_min"] for f in feats]),
        "arrival_c3": arrival_stats([f["c3_min"] for f in feats]),
        "cortes": ladder_cuts(conf, feats, c2, c3, quantities, bk_pts,
                              tp_by_side, ppt),
        "graduada": {(t, k): graduated_prob(feats, "c2_min", t, k)
                     for t in (60.0, 120.0, 180.0) for k in (0.0, 0.5, 1.0)},
        "orden_eventos": order_of_events(feats),
        "ciega_c2": blind_window_pct([f["c2_min"] for f in feats]),
        "ciega_c3": blind_window_pct([f["c3_min"] for f in feats]),
        "atr_exp_c2": atr_expansion_split(feats, "c2_min"),
        "atr_exp_c3": atr_expansion_split(feats, "c3_min"),
    }
    section["recomendacion"] = recomendar(section)
    # RA-1 — columna comparativa de RE-ARMADO en la tabla de cortes, con el
    # MAX_CICLOS del veredicto RA-0v3 (n/s → 1, conservador). INFORMATIVA: R-T1 — el
    # default del estudio sigue corte 1h hasta que RA-2 exista en despacho.
    section["cortes"]["rearmado"] = ladder_cut_rearmado(
        conf, feats, c2, c3, quantities, bk_pts, tp_by_side, ppt,
        section["recomendacion"]["MAX_CICLOS"])
    section["rearmado_max_ciclos"] = section["recomendacion"]["MAX_CICLOS"]
    # `graduada` con llaves tupla no serializa a JSON — versión plana para el front
    section["graduada_flat"] = [
        {"t": int(t), "k": k, **section["graduada"][(t, k)]}
        for t in (60.0, 120.0, 180.0) for k in (0.0, 0.5, 1.0)]
    del section["graduada"]
    return section


# ---------------------------------------------------------------------------
# Orquestación por activo + main (imprime todas las tablas)
# ---------------------------------------------------------------------------

# Config VIGENTE por activo (server DB 2026-07-15) + $/punto conocido.
ACTIVOS = {
    "ES":  {"master": "ListaDeOperaciones/LO130726/ES5m_ConfNormal_TC_TSR_130726.csv",
            "holc": "_ntbridge_0714/ES_5m.csv", "c2": 1.64, "c3": 3.28,
            "quantities": [5, 3, 2], "bk_pts": 90.0,
            "tp": {"long": 8.0, "short": 7.0}, "ppt": 50.0},
    "GC":  {"master": "ListaDeOperaciones/LO130726/GC5m_ContraNormal_ST_WeakConf_130726.csv",
            "holc": "_ntbridge_0714/GC_5m.csv", "c2": 3.63, "c3": 7.26,
            "quantities": [6, 3, 1], "bk_pts": 30.0,
            "tp": {"long": 15.0, "short": 28.5}, "ppt": 100.0},
    "6E":  {"master": "ListaDeOperaciones/LO130726/6E5m_ConfStrong_NC_WeakConf_130726.csv",
            "holc": "_ntbridge_0714/6E_5m.csv", "c2": 1.84, "c3": 3.67,
            "quantities": [5, 3, 2], "bk_pts": 0.02,
            "tp": {"long": 14.5, "short": 7.0}, "ppt": 125000.0},
    "RTY": {"master": "ListaDeOperaciones/LO130726/RTY15m_ConfNormal_NC_TST_130726.csv",
            "holc": "_ntbridge_0714/RTY_15m.csv", "c2": 3.92, "c3": 7.84,
            "quantities": [5, 3, 2], "bk_pts": 110.0,
            "tp": {"long": 29.5, "short": 25.0}, "ppt": 50.0},
}


def run_activo(activo: str, spec: dict) -> dict:
    """Driver del punto 6 (validación cruzada): enriquece el master del activo y
    corre la MISMA `piernas_section` que run_for_clave — cero lógica duplicada."""
    trades, keys, idx, bars, off, cont = enrich_master(
        spec["master"], spec["holc"], activo)
    sec = piernas_section(trades, keys, idx, bars, off, c2=spec["c2"],
                          c3=spec["c3"], quantities=spec["quantities"],
                          bk_pts=spec["bk_pts"], tp_by_side=spec["tp"],
                          ppt=spec["ppt"])
    sec["activo"] = activo
    sec["contencion_pct"] = cont
    sec["graduada"] = {(g["t"] * 1.0, g["k"]): g for g in sec["graduada_flat"]}
    return sec


def _m(mt):
    return (f"net {mt.get('net_usd'):>10,.0f} · PF {mt.get('pf')} · "
            f"DD {mt.get('max_dd_usd'):>9,.0f} · peor {mt.get('peor_trade_usd'):>9,.0f} "
            f"· part {mt.get('n')}")


def main():
    for activo, spec in ACTIVOS.items():
        r = run_activo(activo, spec)
        print("=" * 78)
        print(f"■ {activo}  n={r['n']}  contención={r['contencion_pct']}%  "
              f"(C2={spec['c2']}× C3={spec['c3']}×ATR · bk={spec['bk_pts']} · "
              f"q={spec['quantities']})")
        for leg in ("c2", "c3"):
            a = r[f"arrival_{leg}"]
            print(f"  {leg.upper()} llegada: toca {a['touch_rate_pct']}% · med "
                  f"{a['mediana_min']}m p90 {a['p90_min']}m p95 {a['p95_min']}m · "
                  f"≤1h {a['pct_le_1h']}% ≤2h {a['pct_le_2h']}% ≤3h {a['pct_le_3h']}% "
                  f"nunca {a['pct_nunca']}%")
        print("  R-RA3 graduada P(toca C2 luego | sin toque y ≥k×ATR fav):")
        for tt in (60.0, 120.0, 180.0):
            row = " · ".join(
                f"k{k}: {r['graduada'][(tt, k)]['p_toque_luego_pct']}%"
                f"(n{r['graduada'][(tt, k)]['n_cond']})" for k in (0.0, 0.5, 1.0))
            print(f"    t={int(tt)}m → {row}")
        oe = r["orden_eventos"]
        print(f"  R-RA6 orden: C2 huérfana {oe['pct_c2_huerfana']}% · "
              f"C3 huérfana {oe['pct_c3_huerfana']}%")
        print(f"  Ventana ciega: C2 {r['ciega_c2']['pct_en_ciega']}% · "
              f"C3 {r['ciega_c3']['pct_en_ciega']}% de los toques")
        ae = r["atr_exp_c2"]
        print(f"  R-RA7 ATR_t/ATR_señal tardíos C2 (n{ae['n_tardios']}): "
              f"gan med {ae['ganadores']['atr_ratio_med']} (n{ae['ganadores']['n']}) · "
              f"perd med {ae['perdedores']['atr_ratio_med']} (n{ae['perdedores']['n']})")
        print("  PREGUNTA DE ORO (ladder por corte):")
        prev = None
        for label in ("1h", "2h", "3h", "duracion"):
            mt = r["cortes"][label]
            marg = ("" if prev is None
                    else f"  Δnet {mt['net_usd'] - prev:+,.0f}")
            print(f"    {label:9} {_m(mt)}{marg}")
            prev = mt["net_usd"]


if __name__ == "__main__":
    main()
