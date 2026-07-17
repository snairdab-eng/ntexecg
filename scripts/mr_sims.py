#!/usr/bin/env python3
"""mr_sims — Motor de Riesgo, fase MR-2: estudios de riesgo (núcleo PURO).

Simuladores en el dominio PUNTOS/USD del contrato (1 ES mini = $50/pt =
10 MES), a TAMAÑO FIJO — Directiva 3.1: el riesgo se controla con BACKSTOP
catastrófico + escalonada, NO con sizing por equity. Sin I/O ni DB: recibe
trades enriquecidos (del núcleo del Lab vía nt_riesgo) y devuelve dicts.

Estudios (SPEC §5 + Directiva 3):
  1. mae_floor_study      — suelo del SL (MAE→ATR de las ganadoras) + SL duro
                            ×ATR corrido SOLO para mostrarlo DESCARTADO.
  2. backstop_sweep       — backstop catastrófico en $ fijos (el airbag),
                            con estrés de gap (el hueco puede atravesarlo).
  3. eval_config / build_configs — escalera por MAE: barrido CONJUNTO sobre
                            profundidades ×ATR × distribución de contratos ×
                            nº de piernas (2-3), total FIJO en 10 micros
                            (ladder_grid; alta participación de primera
                            clase) + balanceada + Config A, ± backstop ± TP.
  4. tp_nominal_study     — dónde cierra LuxAlgo sus ganadoras (por lado) →
                            TP NOMINAL por encima del p95/p99 (que cierre
                            LuxAlgo; el TP solo satisface TradersPost).
                            TP-meta (asimétrico) SOLO informativo.
  5. ls_asymmetry         — asimetría Long/Short + give-backs.
  6. reconcile_fills      — tasas de fill de la escalera (MAE, todo el trade)
                            vs pullback del Lab (ventana 180 min).
  7. gate_config          — gating automático: supera la base (score
                            net/maxDD) Y sobrevive OOS (ΔPF out > 0);
                            net-negativos = "descartado – no aporta".

Modelo de la escalera (validado contra la referencia ES):
  - Piernas ancladas al precio de SEÑAL: pierna a d×ATR llena ⟺ el trade
    retrocedió d×ATR (mae_atr ≥ d — el MAE de LuxAlgo es intra-trade, así
    que "límite trabajando hasta la salida" ≡ fill por MAE).
  - Backstop = stop de PRECIO a B pts de la señal (no ×ATR): pierna llenada
    pierde (B − d·ATR) pts (+gap). Peor trade Config A referencia −$3,328 ✓.
  - TP anclado a la señal: pierna gana (tp + d)·ATR pts. Stop manda si el
    trade alcanzó ambos (conservador); pierna profunda + TP en el mismo
    trade se cuenta con la pierna llenando primero (se marca ambigüedad).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median

# Núcleo compartido (Directiva 1): agregación unit-agnóstica del Lab y los
# MISMOS estimadores que el estudio de pullback vivo (pctl y el cancel_after
# de NX-17: min(3600, p90·60+60) — no se inventa un segundo p90).
from app.services.lab_metrics import LOW_N_OUT, aggregate
from scripts.pullback_timing import pctl, suggest_cancel_after


# ---------------------------------------------------------------------------
# Métricas USD (línea base y por config) — reusa lab_metrics.aggregate
# ---------------------------------------------------------------------------

def metrics_usd(pnls: list[float]) -> dict:
    """Métricas en USD reusando `aggregate` (unit-agnóstico: entra USD → sale
    USD) + lo que el núcleo no trae: brutas y DD% sobre high-water mark."""
    m = aggregate(pnls)          # claves *_pct, valores en unidades de entrada
    if m["n"] == 0:
        return {"n": 0}
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    cum = peak = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
    max_dd = abs(m["max_dd_pct"])
    return {
        "n": m["n"],
        "ganadores": len(wins),
        "n_perdedores": len(losses),          # LX-7: PF honesto en muestras filtradas
        "wr_pct": m["wr"],
        "pf": m["pf"],
        "ganancia_bruta_usd": round(sum(wins), 2),
        "perdida_bruta_usd": round(abs(sum(losses)), 2),
        "net_usd": round(sum(pnls), 2),
        "max_dd_usd": round(max_dd, 2),
        # Convención NTEXECG: DD% = MaxDD$ / pico de equity del periodo (HWM)
        "max_dd_pct_hwm": (round(100 * max_dd / peak, 2) if peak > 0 else None),
        "peor_trade_usd": m["worst_pct"],
        "promedio_usd": m["expectancy_pct"],
    }


# ---------------------------------------------------------------------------
# Dominio de simulación
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SimTrade:
    """Trade en el dominio puntos/USD (1 contrato del listado)."""
    number: int
    side: str                # "long" | "short"
    in_sample: bool
    entry_price: float
    atr_pts: float           # ATR(14) en puntos en la entrada
    mae_pts: float           # excursión adversa máx (pts, ≥0)
    mfe_pts: float           # excursión favorable máx (pts, ≥0)
    native_pnl_usd: float    # desenlace nativo de LuxAlgo ($ del contrato)
    atr_estimado: bool = False
    # Minutos al primer toque por nivel ("1.0" → min, el t_pb_touch del Lab,
    # ventana 180 min). None = sin datos de tiempo (cola con ATR estimado o
    # estudio sin pullback) → los fills con corte caen al MAE (aprox).
    pb_touch_min: dict | None = None

    @property
    def mae_atr(self) -> float:
        return self.mae_pts / self.atr_pts

    @property
    def mfe_atr(self) -> float:
        return self.mfe_pts / self.atr_pts

    def native_pnl_pts(self, ppt: float) -> float:
        return self.native_pnl_usd / ppt


def from_trades(trades, ppt: float, estimated_ids: set[int] | None = None,
                ) -> list[SimTrade]:
    """Trades del Lab (parse+enrich) → SimTrades. Universo = con ATR > 0
    (mismo criterio que el Lab; los sin cobertura quedan fuera de los sims
    pero SIEMPRE dentro de la línea base del reporte)."""
    est = estimated_ids or set()
    out = []
    for t in trades:
        if not t.atr_entry or t.atr_entry <= 0:
            continue
        if getattr(t, "no_contenido", False):      # LX-13 — outlier de roll: su
            continue                                # intrabar envenena las derivaciones
        out.append(SimTrade(
            number=t.number, side=t.side, in_sample=t.in_sample,
            entry_price=t.entry_price, atr_pts=t.atr_entry,
            mae_pts=t.mae_pct / 100.0 * t.entry_price,
            mfe_pts=t.mfe_pct / 100.0 * t.entry_price,
            native_pnl_usd=t.pnl_usd,
            atr_estimado=t.number in est,
            # dict vacío = pullback_study no corrió / trade sin barras →
            # None (fallback MAE con el corte, marcado aprox)
            pb_touch_min=dict(t.t_pb_touch) if t.t_pb_touch else None,
        ))
    return out


@dataclass(frozen=True)
class HaircutCfg:
    """Haircut conservador (Directiva 3.3). Defaults 0 = paridad con la
    referencia (que corre sin comisiones/slippage); el estrés de gap del
    backstop se reporta SIEMPRE (GAP_STRESS_PTS)."""
    comision_rt_usd: float = 0.0   # por contrato completo, round-turn
    slip_pts: float = 0.0          # fricción por pierna llenada
    gap_pts: float = 0.0           # deslizamiento del backstop (hueco)


GAP_STRESS_PTS = (0.0, 10.0, 25.0)    # estrés del "peor trade" con gap


# ---------------------------------------------------------------------------
# 1. Suelo del SL (MAE→ATR de las ganadoras) + SL duro ×ATR (DESCARTADO)
# ---------------------------------------------------------------------------

SL_DURO_GRID = (6.0, 8.0, 10.0, 12.0, 14.0, 16.0)


def mae_floor_study(sts: list[SimTrade], ppt: float,
                    hc: HaircutCfg | None = None) -> dict:
    hc = hc or HaircutCfg()
    winners = [st.mae_atr for st in sts if st.native_pnl_usd > 0]
    winners.sort()
    base_net = sum(st.native_pnl_usd for st in sts)
    sl_duro = []
    for k in SL_DURO_GRID:
        pnls = [(-(k * st.atr_pts + hc.gap_pts) * ppt
                 if st.mae_atr >= k else st.native_pnl_usd)
                for st in sts]
        m = metrics_usd(pnls)
        cortadas = (100 * sum(1 for w in winners if w >= k) / len(winners)
                    if winners else None)
        sl_duro.append({
            "k_atr": k, "net_usd": m["net_usd"],
            "delta_net_usd": round(m["net_usd"] - base_net, 2),
            "pf": m["pf"], "max_dd_usd": m["max_dd_usd"],
            "ganadoras_cortadas_pct": (round(cortadas, 1)
                                       if cortadas is not None else None),
            "estado": ("descartado – no aporta"
                       if m["net_usd"] <= base_net else "aporta"),
        })
    return {
        "ganadoras_mae_atr": {
            "n": len(winners),
            "mediana": round(pctl(winners, 0.5), 2) if winners else None,
            "media": (round(sum(winners) / len(winners), 2)
                      if winners else None),
            "p90": round(pctl(winners, 0.9), 2) if winners else None,
            "p95": round(pctl(winners, 0.95), 2) if winners else None,
            "max": round(max(winners), 2) if winners else None,
        },
        "sl_duro_x_atr": sl_duro,
        "veredicto": ("SL duro ×ATR descartado (net-negativo en toda la "
                      "rejilla): mata las ganadoras que aguantan pullback"
                      if all(r["estado"].startswith("descartado")
                             for r in sl_duro) else
                      "revisar: algún SL duro aporta en este listado"),
    }


# ---------------------------------------------------------------------------
# 2. Backstop catastrófico en $ fijos (el airbag)
# ---------------------------------------------------------------------------

BACKSTOP_GRID_USD = (2000.0, 2500.0, 3000.0, 3500.0, 4000.0, 4500.0,
                     5000.0, 5500.0, 6000.0, 6500.0, 7000.0, 7500.0, 8000.0)


def _stop_outcomes(sts: list[SimTrade], b_pts: float, ppt: float,
                   hc: HaircutCfg) -> list[float]:
    return [(-(b_pts + hc.gap_pts) * ppt - hc.comision_rt_usd
             if st.mae_pts >= b_pts
             else st.native_pnl_usd - hc.comision_rt_usd)
            for st in sts]


def backstop_sweep(sts: list[SimTrade], ppt: float,
                   hc: HaircutCfg | None = None,
                   grid_usd: tuple = BACKSTOP_GRID_USD) -> dict:
    hc = hc or HaircutCfg()
    base = metrics_usd([st.native_pnl_usd for st in sts])
    atr_med = median(st.atr_pts for st in sts)
    rows = []
    for usd in grid_usd:
        b_pts = usd / ppt
        pnls = _stop_outcomes(sts, b_pts, ppt, hc)
        m = metrics_usd(pnls)
        tocados = sum(1 for st in sts if st.mae_pts >= b_pts)
        gap_stress = {
            str(g): round(min(_stop_outcomes(
                sts, b_pts, ppt,
                HaircutCfg(hc.comision_rt_usd, hc.slip_pts, g))), 2)
            for g in GAP_STRESS_PTS
        }
        score = (round(m["net_usd"] / m["max_dd_usd"], 2)
                 if m["max_dd_usd"] else None)
        rows.append({
            "backstop_usd": usd,
            "backstop_pts": round(b_pts, 2),
            "x_atr_mediana": round(b_pts / atr_med, 1),
            "tocados": tocados,
            "net_usd": m["net_usd"],
            "delta_net_usd": round(m["net_usd"] - base["net_usd"], 2),
            "pf": m["pf"],
            "max_dd_usd": m["max_dd_usd"],
            "delta_dd_pct": (round(100 * (m["max_dd_usd"]
                                          - base["max_dd_usd"])
                                   / base["max_dd_usd"], 1)
                             if base["max_dd_usd"] else None),
            "peor_trade_usd": m["peor_trade_usd"],
            "peor_con_gap_usd": gap_stress,
            "score_net_dd": score,
        })
    # Óptimo = mayor score net/maxDD (PnL + control de pérdidas — SPEC §5.6):
    # el backstop es un dispositivo de RIESGO, puede ceder algo de net a
    # cambio de recortar el DD y el peor trade. El Δnet queda a la vista.
    base_score = (base["net_usd"] / base["max_dd_usd"]
                  if base["max_dd_usd"] else None)
    mejores = [r for r in rows
               if r["score_net_dd"] is not None and base_score is not None
               and r["score_net_dd"] > base_score]
    optimo = max(mejores, key=lambda r: r["score_net_dd"]) if mejores else None
    return {"grid": rows, "optimo": optimo,
            "score_base": round(base_score, 2) if base_score else None,
            "atr_mediana_pts": round(atr_med, 2)}


# ---------------------------------------------------------------------------
# 3. Escalera por MAE (laddering) — configs y evaluación
# ---------------------------------------------------------------------------

BALANCEADA = tuple((d, 0.1) for d in (0.5, 1.0, 2.0, 3.0, 3.5, 4.5,
                                      5.0, 5.5, 6.0, 6.5))
CONFIG_A = ((6.5, 0.6), (7.0, 0.4))
SENAL = ((0.0, 1.0),)

# ── Barrido CONJUNTO de la escalera (Directiva 3.1 actualizada) ──
# Tres grados de libertad: (a) profundidades ×ATR de cada pierna, (b) la
# DISTRIBUCIÓN de contratos por pierna (60/40 era solo un ejemplo), y (c) el
# nº de piernas (2 o 3). El TOTAL es SIEMPRE 10 micros = 1 mini, para
# comparar 1:1 contra la línea base de LuxAlgo — el barrido reparte esos 10,
# nunca cambia el tamaño. Alta participación (primera pierna a mercado o
# somera ≤0.5×) de PRIMERA CLASE.
TOTAL_MICROS = 10
LADDER_D1_2 = (0.0, 0.25, 0.5, 1.0)
LADDER_D2_2 = (0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 6.5, 7.0)
LADDER_DIST_2 = ((7, 3), (6, 4), (5, 5), (4, 6), (3, 7))
LADDER_D1_3 = (0.0, 0.25, 0.5)
LADDER_D2_3 = (0.5, 1.0, 1.5, 2.0)
LADDER_D3_3 = (1.5, 2.0, 3.0, 4.0, 5.0, 6.5)
LADDER_DIST_3 = ((5, 3, 2), (4, 3, 3), (3, 3, 4))


def ladder_grid() -> list[tuple[str, tuple, tuple]]:
    """[(nombre, legs, etiquetas)] del barrido conjunto. Pesos = micros/10
    (la suma de contratos es SIEMPRE TOTAL_MICROS)."""
    out: list[tuple[str, tuple, tuple]] = []

    def add(depths: tuple, dist: tuple) -> None:
        assert sum(dist) == TOTAL_MICROS
        legs = tuple((d, c / TOTAL_MICROS) for d, c in zip(depths, dist))
        nombre = (f"{'+'.join(str(c) for c in dist)} MES @ "
                  f"{'/'.join(f'{d:g}' for d in depths)}× + backstop")
        tags = ["barrido", f"{len(depths)}_piernas"]
        if depths[0] <= 0.5:
            tags.append("alta_participacion")
        out.append((nombre, legs, tuple(tags)))

    for d1 in LADDER_D1_2:
        for d2 in LADDER_D2_2:
            if d2 <= d1:
                continue
            for dist in LADDER_DIST_2:
                add((d1, d2), dist)
    for d1 in LADDER_D1_3:
        for d2 in LADDER_D2_3:
            if d2 <= d1:
                continue
            for d3 in LADDER_D3_3:
                if d3 <= d2:
                    continue
                for dist in LADDER_DIST_3:
                    add((d1, d2, d3), dist)
    return out


# Máximo duro de TradersPost para cancelAfter (verificado en su doc:
# "cancelAfter must be between 1 and 3600 seconds") — el corte del estudio
# nunca puede prometer más que esto.
CANCEL_AFTER_MAX_S = 3600.0


# RA-1 — modelo de RE-ARMADO (SPEC_Rearmado_Piernas §1-§3): la pierna límite se
# re-envía cada ciclo de REARM_CYCLE_MIN; la orden vive REARM_LIVE_MIN y el re-envío
# ocurre al minuto 61-62 (ventana ciega [REARM_LIVE_MIN, REARM_CYCLE_MIN) = fill
# perdido honesto). Mismos números que el estudio de llegada (blind_window_pct).
REARM_CYCLE_MIN = 62.0
REARM_LIVE_MIN = 60.0


def leg_filled(st: SimTrade, depth: float,
               cancel_after_s: float | None = None,
               rearm_ciclos: int | None = None) -> tuple[bool, bool]:
    """(llena, aprox) de una pierna límite a `depth`×ATR. Tres modos:

    · Sin corte (cancel_after_s=None, rearm_ciclos=None): MAE de todo el trade
      (límite trabajando hasta la salida — el modelo original, TECHO del estudio).
    · Con corte (cancel_after_s): el toque cacheado (t_pb_touch, MINUTOS) debe
      llegar dentro de cancel_after (SEGUNDOS) — TradersPost cancela a los
      cancel_after s (máx 3600). Es la POLÍTICA DE DESPACHO VIGENTE (R-T1).
    · RA-1 RE-ARMADO (rearm_ciclos=N, precede a cancel_after_s): la pierna se
      re-arma cada ciclo hasta N ciclos; el toque llena si ocurre ≤ N×REARM_CYCLE_MIN
      Y NO cae en la ventana ciega [REARM_LIVE_MIN, REARM_CYCLE_MIN) de su ciclo.
      El t_pb_touch ya está acotado a la VIDA del trade (touch_minutes entrada→
      salida), así que la duración está implícita. Columna INFORMATIVA del estudio.

    Trade sin datos de tiempo (cola con ATR estimado) → fallback al MAE, marcado
    aprox (optimista, contado aparte)."""
    if depth <= 0:
        return True, False
    if st.mae_atr < depth:
        return False, False                # nunca tocó (ni sin corte)
    if rearm_ciclos is not None:           # RA-1 — modo re-armado (precede al corte)
        if st.pb_touch_min is None:
            return True, True              # sin tiempos → MAE (aprox)
        t_min = st.pb_touch_min.get(str(float(depth)))
        if t_min is None:
            return False, False            # tocó, pero fuera de la ventana
        if t_min > rearm_ciclos * REARM_CYCLE_MIN:
            return False, False            # más allá de MAX_CICLOS (ya no se re-arma)
        en_ciega = REARM_LIVE_MIN <= (t_min % REARM_CYCLE_MIN) < REARM_CYCLE_MIN
        return (not en_ciega), False       # ventana ciega = fill perdido honesto
    if cancel_after_s is None:
        return True, False
    if st.pb_touch_min is None:
        return True, True                  # sin tiempos → MAE (aprox)
    t_min = st.pb_touch_min.get(str(float(depth)))
    if t_min is None:
        return False, False                # tocó, pero fuera de la ventana
    return t_min * 60.0 <= cancel_after_s, False


def ladder_outcome(st: SimTrade, legs: tuple, b_pts: float | None,
                   tp_atr_by_side: dict | None, ppt: float,
                   hc: HaircutCfg,
                   cancel_after_s: float | None = None,
                   rearm_ciclos: int | None = None,
                   ) -> tuple[float, float, bool]:
    """(pnl_usd, peso_llenado, ambigüedad pierna↔TP) de un trade.
    Fills por MAE (techo), con corte de cancel_after, o RA-1 re-armado
    (rearm_ciclos) — todos vía `leg_filled`; stop manda sobre TP (conservador)."""
    tp_atr = (tp_atr_by_side or {}).get(st.side)
    stopped = b_pts is not None and st.mae_pts >= b_pts
    tp_hit = (not stopped and tp_atr is not None and st.mfe_atr >= tp_atr)
    acc = filled_w = 0.0
    ambiguous = False
    for d, w in legs:
        if not leg_filled(st, d, cancel_after_s, rearm_ciclos)[0]:
            continue                       # la pierna no llenó (a tiempo)
        filled_w += w
        if stopped:
            pnl_pts = -(b_pts + hc.gap_pts - d * st.atr_pts)
        elif tp_hit:
            pnl_pts = (tp_atr + d) * st.atr_pts
            if d > 0:
                ambiguous = True           # orden asumido: pierna → TP
        else:
            pnl_pts = st.native_pnl_pts(ppt) + d * st.atr_pts
        acc += w * (pnl_pts - hc.slip_pts)
    usd = acc * ppt - hc.comision_rt_usd * filled_w
    return usd, filled_w, ambiguous


def config_outcomes(sts: list[SimTrade], legs: tuple,
                    b_pts: float | None, tp_by_side: dict | None,
                    ppt: float, hc: HaircutCfg,
                    solo_lado: str | None = None,
                    cancel_after_s: float | None = None,
                    ) -> list[tuple[SimTrade, float, bool, bool]]:
    """Serie CRONOLÓGICA completa de una config: [(trade, usd, participó,
    ambiguo)] con 0.0 en lo no-participado — la única fuente para
    eval_config, el walk-forward y el estrés de piernas."""
    out: list[tuple[SimTrade, float, bool, bool]] = []
    for st in sts:
        if solo_lado and st.side != solo_lado:
            out.append((st, 0.0, False, False))
            continue
        usd, fw, amb = ladder_outcome(st, legs, b_pts, tp_by_side, ppt, hc,
                                      cancel_after_s)
        out.append((st, usd if fw > 0 else 0.0, fw > 0, amb))
    return out


def eval_config(sts: list[SimTrade], nombre: str, legs: tuple,
                backstop_usd: float | None, ppt: float,
                tp_by_side: dict | None = None,
                hc: HaircutCfg | None = None,
                solo_lado: str | None = None,
                etiquetas: tuple = (),
                cancel_after_s: float | None = None) -> dict:
    """Evalúa una config sobre TODO el universo (no-participado = 0.0 para
    que net/DD sean comparables 1:1 contra la base en el mismo periodo).
    WR reportado = sobre trades PARTICIPADOS (documentado)."""
    hc = hc or HaircutCfg()
    b_pts = backstop_usd / ppt if backstop_usd else None
    outcomes = config_outcomes(sts, legs, b_pts, tp_by_side, ppt, hc,
                               solo_lado, cancel_after_s)
    ambiguos = sum(1 for *_, amb in outcomes if amb)

    def blk(sel):
        return metrics_usd([u for _, u, _, _ in sel])

    total = blk(outcomes)
    participados = [(st, u) for st, u, p, _ in outcomes if p]
    n_part = len(participados)
    if n_part:
        total["wr_pct"] = round(
            100 * sum(1 for _, u in participados if u > 0) / n_part, 1)
    inb = blk([o for o in outcomes if o[0].in_sample])
    outb = blk([o for o in outcomes if not o[0].in_sample])
    n_part_out = sum(1 for st, _, p, _ in outcomes
                     if p and not st.in_sample)
    return {
        "nombre": nombre,
        "legs": [{"depth_atr": d, "peso": round(w, 4)} for d, w in legs],
        "n_piernas": sum(1 for d, w in legs if w > 0),
        "backstop_usd": backstop_usd,
        "tp_por_lado_atr": tp_by_side,
        "solo_lado": solo_lado,
        "etiquetas": list(etiquetas),
        "participacion_pct": round(100 * n_part / len(sts), 1) if sts else None,
        "n_participados": n_part,
        "n_participados_out": n_part_out,
        "low_n_out": n_part_out < LOW_N_OUT,
        "ambiguos_tp": ambiguos,
        "total": total, "in": inb, "out": outb,
    }


def build_configs(sts: list[SimTrade], ppt: float, backstop_usd: float,
                  tp_nominal: dict | None, tp_meta: dict | None,
                  hc: HaircutCfg | None = None,
                  cancel_after_s: float | None = None) -> list[dict]:
    """La parrilla de configs del estudio. Alta participación de primera
    clase (la 60/40 la DECIDE el estudio — Directiva 3.1); TP-meta marcado
    informativo (la recomendación honra 'que cierre LuxAlgo')."""
    hc = hc or HaircutCfg()
    cfgs: list[dict] = []

    def add(nombre, legs, b=backstop_usd, tp=None, lado=None, tags=()):
        cfgs.append(eval_config(sts, nombre, legs, b, ppt, tp, hc,
                                lado, tags, cancel_after_s))

    add("señal + backstop (sin escalera)", SENAL)
    add("balanceada + backstop", BALANCEADA, tags=("referencia",))
    add("Config A (6+4 MES @ 6.5/7.0×) + backstop", CONFIG_A,
        tags=("referencia", "profunda"))
    for nombre, legs, tags in ladder_grid():
        add(nombre, legs, tags=tags)
    if tp_nominal:
        add("balanceada + backstop + TP nominal (px arriba)", BALANCEADA,
            tp=tp_nominal, tags=("recomendable", "tp_nominal"))
    if tp_meta:
        add("balanceada + backstop + TP-meta (INFORMATIVO)", BALANCEADA,
            tp=tp_meta, tags=("informativo", "tp_meta"))
        add("Config A + backstop + TP-meta (INFORMATIVO)", CONFIG_A,
            tp=tp_meta, tags=("informativo", "tp_meta", "profunda"))
    add("solo largos + balanceada + backstop", BALANCEADA, lado="long",
        tags=("solo_largos",))
    add("solo largos + Config A + backstop", CONFIG_A, lado="long",
        tags=("solo_largos", "profunda"))
    return cfgs


def all_ladder_depths() -> tuple:
    """TODAS las profundidades ×ATR que usa el barrido (grid + balanceada +
    Config A) — los niveles que el pullback del Lab debe rastrear para que
    el corte de cancel_after tenga el toque cacheado de cada pierna."""
    ds = {d for _, legs, _ in ladder_grid() for d, _ in legs if d > 0}
    ds |= {d for d, _ in BALANCEADA} | {d for d, _ in CONFIG_A}
    return tuple(sorted(ds))


def fills_cutoff_study(sts: list[SimTrade],
                       cancel_after_s: float) -> dict:
    """Fill%% por profundidad: "alguna vez llena" (MAE, el modelo original)
    vs DENTRO de cancel_after (t_pb_touch ≤ corte) — dónde mueren los fills
    en producción. HONESTIDAD: los números con corte son MÁS BAJOS por
    construcción; esos son los reales (TradersPost cancela la entrada a los
    cancel_after s, máximo duro 3600)."""
    niveles = []
    n = len(sts)
    n_aprox = sum(1 for st in sts if st.pb_touch_min is None)
    for lvl in all_ladder_depths():
        ever = sum(1 for st in sts if st.mae_atr >= lvl)
        corte = sum(1 for st in sts if leg_filled(st, lvl, cancel_after_s)[0])
        touches = [st.pb_touch_min[str(float(lvl))] for st in sts
                   if st.pb_touch_min is not None
                   and str(float(lvl)) in st.pb_touch_min]
        niveles.append({
            "nivel_atr": lvl,
            "fill_sin_corte_pct": round(100 * ever / n, 1) if n else None,
            "fill_con_corte_pct": round(100 * corte / n, 1) if n else None,
            "n_sin_corte": ever,
            "n_con_corte": corte,
            "retencion": round(corte / ever, 2) if ever else None,
            "t_med_min": (round(pctl(touches, 0.5), 0)
                          if touches else None),
            "t_p90_min": (round(pctl(touches, 0.9), 0)
                          if touches else None),
            "cancel_after_sugerido_s": suggest_cancel_after(touches),
        })
    # Tope natural: el nivel más hondo que aún llena de forma significativa
    # dentro del corte (fill ≥ 10% del universo Y retiene ≥ la mitad de sus
    # fills sin corte).
    tope = None
    for row in niveles:
        if (row["fill_con_corte_pct"] is not None
                and row["fill_con_corte_pct"] >= 10.0
                and (row["retencion"] or 0) >= 0.5):
            tope = row["nivel_atr"]
    return {
        "cancel_after_s": cancel_after_s,
        "niveles": niveles,
        "tope_natural_atr": tope,
        "n_sin_datos_tiempo": n_aprox,
        "nota": ("fills con corte = los REALES de producción (TradersPost "
                 "cancela la entrada a los cancel_after s; máx duro 3600). "
                 "Más bajos que el 'alguna vez llena' por construcción — "
                 "ese es el punto, no maquillaje. Trades sin datos de "
                 "tiempo (cola con ATR estimado) usan el MAE (optimista, "
                 "contados en n_sin_datos_tiempo)."),
    }


# ---------------------------------------------------------------------------
# 4. TP nominal por ENCIMA del cierre de LuxAlgo (+ TP-meta informativo)
# ---------------------------------------------------------------------------

TP_META_GRID_L = (4.0, 4.5, 5.0, 5.5, 6.0, 6.5)
TP_META_GRID_S = (0.5, 1.0, 1.5, 2.0)

# TP nominal SIEMPRE (R-obs-2): TradersPost exige bracket válido en toda
# entrada; el TP nominal debe existir por lado AUNQUE falte muestra para un
# p99 fiable. Con menos de MIN_GANADORAS_P99 ganadoras en el lado, cae a un
# default ANCHO documentado (15×ATR — muy por encima de cualquier cierre
# típico de LuxAlgo: casi nunca dispara, que cierre LuxAlgo). NUNCA cae al
# TP ajustado k×ATR (ese estrangula al motor — descartado por diseño).
TP_NOMINAL_FALLBACK_ATR = 15.0
MIN_GANADORAS_P99 = 10


def _ceil_half(x: float) -> float:
    return math.ceil(x * 2.0) / 2.0


def tp_nominal_study(sts: list[SimTrade], ppt: float,
                     hc: HaircutCfg | None = None,
                     meta_legs: tuple = SENAL,
                     meta_b_usd: float | None = None,
                     cancel_after_s: float | None = None) -> dict:
    """Mide DÓNDE CIERRA LuxAlgo sus ganadoras (excursión al cierre, ×ATR,
    por lado) y fija el TP NOMINAL por encima del p99 — para que casi nunca
    dispare antes que LuxAlgo (solo satisface TradersPost). El TP-meta
    (asimétrico, el de la referencia) se reporta SOLO informativo, evaluado
    sobre el STACK (meta_legs + backstop): el TP interactúa con la escalera
    (la pierna profunda que sale en el TP gana (tp+d)×ATR), no con la señal
    sola."""
    hc = hc or HaircutCfg()
    por_lado: dict[str, dict] = {}
    tp_nominal: dict[str, float] = {}
    for lado in ("long", "short"):
        del_lado = [st for st in sts if st.side == lado]
        ganadoras = [st for st in del_lado if st.native_pnl_usd > 0]
        exc = sorted(st.native_pnl_pts(ppt) / st.atr_pts for st in ganadoras)
        fallback = len(exc) < MIN_GANADORAS_P99
        p95 = round(pctl(exc, 0.95), 4) if exc else None
        p99 = round(pctl(exc, 0.99), 4) if exc else None
        if not fallback:
            tp = _ceil_half(p99)
            if tp <= p99:
                tp += 0.5                  # estrictamente POR ENCIMA del p99
        else:
            # R-obs-2: muestra chica → default ANCHO documentado, nunca el
            # TP ajustado k×ATR. El TP nominal SIEMPRE existe (bracket).
            tp = TP_NOMINAL_FALLBACK_ATR
        tp_nominal[lado] = tp
        dispararia = sum(1 for st in del_lado if st.mfe_atr >= tp)
        giveback = sum(st.mfe_pts * ppt - st.native_pnl_usd
                       for st in ganadoras)
        por_lado[lado] = {
            "n_ganadoras": len(ganadoras),
            "tp_nominal_fallback": fallback,
            "cierre_atr": {
                "p50": round(pctl(exc, 0.5), 2) if exc else None,
                "p90": round(pctl(exc, 0.9), 2) if exc else None,
                "p95": round(p95, 2) if p95 is not None else None,
                "p99": round(p99, 2) if p99 is not None else None,
                "max": round(exc[-1], 2) if exc else None,
            },
            "tp_nominal_atr": tp_nominal.get(lado),
            "tp_nominal_dispararia_n": dispararia,
            "tp_nominal_dispararia_pct": (round(100 * dispararia
                                                / len(del_lado), 1)
                                          if del_lado else None),
            "en_la_mesa_usd": round(giveback, 2),    # MFE − salida (ganadoras)
        }
    # TP-meta (informativo): rejilla asimétrica L/S sobre el stack
    grid = []
    for L in TP_META_GRID_L:
        for S in TP_META_GRID_S:
            r = eval_config(sts, f"TP-meta L{L}/S{S}", meta_legs,
                            meta_b_usd, ppt, {"long": L, "short": S}, hc,
                            cancel_after_s=cancel_after_s)
            grid.append({"tp_long": L, "tp_short": S,
                         "net_usd": r["total"].get("net_usd"),
                         "pf": r["total"].get("pf"),
                         "pf_in": r["in"].get("pf"),
                         "pf_out": r["out"].get("pf")})
    mejor = max(grid, key=lambda g: g["net_usd"]) if grid else None
    return {
        "por_lado": por_lado,
        "tp_nominal_atr": tp_nominal,
        "tp_meta_grid": grid,
        "tp_meta_mejor": mejor,
        "nota": ("El TP nominal va POR ENCIMA de donde cierra LuxAlgo "
                 "(p99, por lado): casi nunca dispara — que cierre LuxAlgo. "
                 "El TP-meta es informativo (cuánto habría en la mesa); "
                 "NO es la recomendación."),
    }


# ---------------------------------------------------------------------------
# 5. Asimetría Long/Short + give-backs
# ---------------------------------------------------------------------------

def ls_asymmetry(sts: list[SimTrade]) -> dict:
    out = {}
    for lado in ("long", "short"):
        sel = [st for st in sts if st.side == lado]
        m = metrics_usd([st.native_pnl_usd for st in sel])
        giveback = sum(1 for st in sel
                       if st.native_pnl_usd < 0 and st.mfe_atr >= 3.0)
        m["giveback_perdedores_3atr"] = giveback
        out[lado] = m
    pf_l = out["long"].get("pf")
    pf_s = out["short"].get("pf")
    if pf_l is not None and pf_s is not None:
        if pf_l >= 2 * pf_s:
            out["lectura"] = "motor de LARGOS (cortos casi break-even)"
        elif pf_s >= 2 * pf_l:
            out["lectura"] = "motor de CORTOS (largos casi break-even)"
        else:
            out["lectura"] = "sin asimetría dominante"
    else:
        out["lectura"] = "sin datos comparables"
    return out


# ---------------------------------------------------------------------------
# 5b. Gestión POR LADO — la 4ª palanca (P1b, auditoría 2026-07-06)
# ---------------------------------------------------------------------------

# Lado malo con menos de esto = "muestra chica" → validar en demo
FRAGIL_SIDE_N = 40


def _dd_share_por_lado(sts: list[SimTrade]) -> dict[str, float]:
    """Fracción de las PÉRDIDAS dentro de la ventana del max drawdown de la
    equity nativa (pico→valle) que aporta cada lado — quién "guarda" la
    catástrofe."""
    if not sts:
        return {"long": 0.0, "short": 0.0}
    cums: list[float] = []
    cum = 0.0
    for s in sts:
        cum += s.native_pnl_usd
        cums.append(cum)
    peak_val, peak_i = 0.0, -1
    best_dd, ventana = 0.0, (-1, -1)
    for i, c in enumerate(cums):
        if c > peak_val:
            peak_val, peak_i = c, i
        dd = peak_val - c
        if dd > best_dd:
            best_dd, ventana = dd, (peak_i, i)
    if best_dd <= 0:
        return {"long": 0.0, "short": 0.0}
    perdidas = {"long": 0.0, "short": 0.0}
    for s in sts[ventana[0] + 1: ventana[1] + 1]:
        if s.native_pnl_usd < 0:
            perdidas[s.side] += -s.native_pnl_usd
    total = sum(perdidas.values()) or 1.0
    return {k: round(v / total, 3) for k, v in perdidas.items()}


def side_management(sts: list[SimTrade],
                    ls: dict | None = None) -> dict:
    """Recomendación ESTRUCTURAL de gestión por lado — NO pasa por el
    walk-forward, a propósito: "el lado que pierde dinero y guarda la
    catástrofe debe reducirse" es estructura, no un parámetro optimizado.
    (Y no puede pasar: el lado bueno de YM tiene 100% WR → PF OOS = None →
    jamás 'validaría' por examen OOS.)

    Dispara SOLO cuando un lado es claramente el problema: net ≤ 0 o
    PF < 1. Un lado net-positivo NUNCA se recorta (ES: cortos PF 1.12 con
    el peor trade → nada). La catástrofe (peor trade global en ese lado
    y/o >50% del max DD) decide la fuerza: CORTAR vs REDUCIR. Con caveat
    honesto de muestra (nº de trades del lado malo)."""
    ls = ls or ls_asymmetry(sts)
    dd_share = _dd_share_por_lado(sts)
    perdedores = [s for s in sts if s.native_pnl_usd < 0]
    worst = min(perdedores, key=lambda s: s.native_pnl_usd, default=None)
    out: dict = {"dd_share": dd_share, "recomendacion": None}

    for lado in ("long", "short"):
        m = ls.get(lado) or {}
        if not m.get("n"):
            continue
        net, pf = m.get("net_usd"), m.get("pf")
        pierde = (net is not None and net <= 0) or (pf is not None and pf < 1)
        if not pierde:
            continue
        catastrofe = ((worst is not None and worst.side == lado)
                      or dd_share.get(lado, 0.0) > 0.5)
        bueno = "short" if lado == "long" else "long"
        accion = "cortar" if catastrofe else "reducir"
        n = m["n"]
        chica = n < FRAGIL_SIDE_N
        etq = {"long": "largos", "short": "cortos"}
        motivo = f"{etq[lado]}: net {net:+,.0f} USD, PF {pf if pf is not None else '—'}"
        if catastrofe:
            partes = []
            if worst is not None and worst.side == lado:
                partes.append(f"peor trade {worst.native_pnl_usd:+,.0f}")
            if dd_share.get(lado, 0.0) > 0.5:
                partes.append(f"{dd_share[lado] * 100:.0f}% del max DD")
            motivo += " y concentra la catástrofe (" + ", ".join(partes) + ")"
        if accion == "cortar":
            mecanismo = (f"solo {etq[bueno]} — filtro de lado en la config "
                         f"(paso aparte); el mecanismo vivo hoy es "
                         f"short_size_factor (reduce, no corta)")
            if lado == "long":
                mecanismo = ("solo cortos — requiere mecanismo/config aparte "
                             "(hoy solo existe short_size_factor)")
        else:
            mecanismo = ({"short_size_factor": 0.5} if lado == "short" else
                         "reducir largos requiere mecanismo aparte (hoy solo "
                         "existe short_size_factor)")
        out["recomendacion"] = {
            "lado_malo": lado,
            "lado_bueno": bueno,
            "accion": accion,
            "motivo": motivo,
            "n_lado_malo": n,
            "muestra_chica": chica,
            "efecto_solo_lado_bueno": dict(ls.get(bueno) or {}),
            "mecanismo": mecanismo,
            "caveat": (f"recomendación ESTRUCTURAL — no pasa por el "
                       f"walk-forward (el lado bueno puede tener PF OOS "
                       f"incalculable, p. ej. 100% WR); considera "
                       f"{accion} y valida en demo"
                       + (f" — muestra chica ({n} trades en el lado "
                          f"{etq[lado]})" if chica else
                          f" ({n} trades en el lado {etq[lado]})")),
        }
        break        # un solo lado malo relevante (si pierden ambos, la
                     # estrategia entera es el problema, no un lado)
    return out


# ---------------------------------------------------------------------------
# 5c. Protección de cuenta — estudio IN-SAMPLE, SIN gate OOS (pestaña v2)
# ---------------------------------------------------------------------------

# Un trade que pierde ≥ este % de la cuenta es un DESASTRE (se marca en rojo
# y el estudio busca dejarlo por debajo).
ALARMA_PCT = 10.0

# SL ×ATR más fino que el grid del estudio descartado (aquí el objetivo no es
# net, es capar desastres) — los candidatos se FILTRAN al suelo del MAE de
# ganadoras (deja respirar).
PROTECCION_SL_GRID = (3.0, 3.5, 4.0, 4.5, 5.0, 5.5) + SL_DURO_GRID

ETIQUETA_PROTECCION = ("in-sample, sin validar OOS — para proteger la "
                       "cuenta, NO promesa a futuro")


def _eval_proteccion(sts: list[SimTrade], ppt: float, hc: HaircutCfg,
                     esc_nombre: str, legs: tuple,
                     sl_atr: float | None, b_usd: float | None,
                     tp_by_side: dict | None, lado: str | None,
                     cancel_after_s: float | None = None) -> dict:
    """Un combo de protección — el MISMO modelo de la escalera del estudio
    validado (piernas ancladas a la señal, fills por MAE con corte de
    cancel_after, stop manda sobre TP), con el stop generalizado: SL ×ATR
    (distancia por trade) y/o backstop $ fijo — manda el más cercano.
    Métricas completas de metrics_usd sobre TODA la muestra (sin split)."""
    b_pts = b_usd / ppt if b_usd else None
    pnls: list[float] = []
    participados: list[float] = []
    cortadas = 0
    for st in sts:
        if lado and st.side != lado:
            pnls.append(0.0)
            continue
        stops = [x for x in ((sl_atr * st.atr_pts if sl_atr else None),
                             b_pts) if x is not None]
        stop_pts = min(stops) if stops else None
        stopped = stop_pts is not None and st.mae_pts >= stop_pts
        tp_atr = (tp_by_side or {}).get(st.side)
        tp_hit = (not stopped and tp_atr is not None
                  and st.mfe_atr >= tp_atr)
        acc = fw = 0.0
        for d, w in legs:
            if not leg_filled(st, d, cancel_after_s)[0]:
                continue
            fw += w
            if stopped:
                pnl_pts = -(stop_pts + hc.gap_pts - d * st.atr_pts)
            elif tp_hit:
                pnl_pts = (tp_atr + d) * st.atr_pts
            else:
                pnl_pts = st.native_pnl_pts(ppt) + d * st.atr_pts
            acc += w * (pnl_pts - hc.slip_pts)
        if fw > 0:
            usd = acc * ppt - hc.comision_rt_usd * fw
            pnls.append(usd)
            participados.append(usd)
            if stopped and st.native_pnl_usd > 0:
                cortadas += 1              # ganadora que el stop mató
        else:
            pnls.append(0.0)               # ninguna pierna llenó
    m = metrics_usd(pnls)
    if participados:
        m["wr_pct"] = round(
            100 * sum(1 for p in participados if p > 0) / len(participados), 1)
    ganadoras = sum(1 for st in sts if st.native_pnl_usd > 0
                    and (not lado or st.side == lado))
    es_escalera = len(legs) > 1 or any(d > 0 for d, _ in legs)
    return {
        "escalera": {
            "nombre": esc_nombre,
            "piernas": [{"depth_atr": d, "micros": round(w * TOTAL_MICROS)}
                        for d, w in legs],
        },
        "sl_atr": sl_atr,
        "backstop_usd": b_usd,
        "tp_por_lado_atr": tp_by_side,
        "lado": lado,
        # el TP nominal NO cuenta como palanca: es el bracket obligatorio
        # (siempre presente, casi nunca dispara), no una optimización
        "n_palancas": (int(es_escalera) + int(sl_atr is not None)
                       + int(b_usd is not None) + int(lado is not None)),
        "participacion_pct": (round(100 * len(participados) / len(sts), 1)
                              if sts else None),
        "ganadoras_cortadas_pct": (round(100 * cortadas / ganadoras, 1)
                                   if ganadoras else None),
        "metricas": m,
    }


# Escaleras por defecto del estudio de protección (sin run_studies, p. ej.
# tests unitarios): entrada única (el espejo del crudo) + la balanceada.
PROTECCION_ESCALERAS_BASE = (("entrada única a la señal", SENAL),
                             ("balanceada", BALANCEADA))


def proteccion_study(sts: list[SimTrade], ppt: float,
                     hc: HaircutCfg | None = None,
                     suelo_atr: float | None = None,
                     tp_nominal: dict | None = None,
                     gestion_lado: dict | None = None,
                     escaleras: list[tuple[str, tuple]] | None = None,
                     cancel_after_s: float | None = None) -> dict:
    """Estudio de PROTECCIÓN DE CUENTA — espejo COMPLETO del estudio
    validado, sobre TODA la muestra (R-obs-1): las MISMAS palancas
    (SL ×ATR, backstop $ fijo, escalera niveles+cantidad, TP nominal por
    lado, gestión por lado largos/cortos) y las MISMAS métricas
    (metrics_usd: PF, WR, expectancy, maxDD, net, peor, n + participación).
    La ÚNICA diferencia: sin split OOS y sin gate — la selección es por
    supervivencia > net (proteccion_para_cuenta, cuenta editable).

    El suelo del SL (MAE p95 de las GANADORAS) filtra los frenos: un stop
    más ceñido que el retroceso típico de las ganadoras mata recuperadoras.
    El freno es UNO por combo (SL ×ATR o backstop $ — alternativas, no
    apilados): la recomendación queda accionable. El lado se barre en ambos
    sentidos (largos sí/no, cortos sí/no); el candidato de P1b solo aporta
    el caveat de muestra. El TP nominal va SIEMPRE (bracket, no palanca).

    La SELECCIÓN por cuenta NO vive aquí: los combos se persisten en $
    absolutos y `proteccion_para_cuenta` (pura) elige al instante para la
    cuenta editable — cero segundo cálculo."""
    hc = hc or HaircutCfg()
    if not sts:
        return {"suelo_atr": None, "atr_mediana_pts": None,
                "lado_candidato": None, "tp_nominal_atr": tp_nominal,
                "umbral_alarma_pct": ALARMA_PCT,
                "etiqueta": ETIQUETA_PROTECCION,
                "combos": [], "perdedores": []}
    if suelo_atr is None:
        winners = sorted(st.mae_atr for st in sts if st.native_pnl_usd > 0)
        suelo_atr = round(pctl(winners, 0.95), 2) if winners else None
    atr_med = median(st.atr_pts for st in sts)
    sl_cands = sorted({k for k in PROTECCION_SL_GRID
                       if suelo_atr is None or k >= suelo_atr})
    bk_cands = [b for b in BACKSTOP_GRID_USD
                if suelo_atr is None or (b / ppt) / atr_med >= suelo_atr]
    rec = (gestion_lado or {}).get("recomendacion")
    lado_cand = rec["lado_bueno"] if rec else None

    esc_cands = list(escaleras or PROTECCION_ESCALERAS_BASE)
    frenos: list[tuple[float | None, float | None]] = [(None, None)]
    frenos += [(k, None) for k in sl_cands]
    frenos += [(None, b) for b in bk_cands]

    combos: list[dict] = []
    for nombre, legs in esc_cands:
        for sl, b in frenos:
            for lado in (None, "long", "short"):
                combos.append(_eval_proteccion(
                    sts, ppt, hc, nombre, legs, sl, b, tp_nominal, lado,
                    cancel_after_s))
    perdedores = sorted(
        ({"number": st.number, "side": st.side,
          "pnl_usd": st.native_pnl_usd}
         for st in sts if st.native_pnl_usd < 0),
        key=lambda p: p["pnl_usd"])
    return {
        "suelo_atr": suelo_atr,
        "atr_mediana_pts": round(atr_med, 2),
        "lado_candidato": lado_cand,
        "lado_muestra_chica": bool(rec and rec.get("muestra_chica")),
        "lado_n_malo": rec["n_lado_malo"] if rec else None,
        "tp_nominal_atr": tp_nominal,
        "n_escaleras": len(esc_cands),
        "umbral_alarma_pct": ALARMA_PCT,
        "etiqueta": ETIQUETA_PROTECCION,
        "combos": combos,
        "perdedores": perdedores,
    }


def proteccion_para_cuenta(prot: dict, cuenta_usd: float,
                           crudo_total: dict) -> dict:
    """Selección PURA por tamaño de cuenta (la cuenta editable de la
    pestaña llama esto al vuelo — el barrido pesado ya está persistido).

    PARTICIPACIÓN 100% OBLIGATORIA (R-obs-2, 2026-07-07): el objetivo de
    NTEXECG es capar la pérdida catastrófica, NO filtrar señales — LuxAlgo
    ya tiene el edge. Solo son elegibles combos que participan en TODOS los
    trades; "sobrevivir dejando de operar" (escaleras que no llenan, lados
    bloqueados) queda fuera de la recomendación de protección. Sin ningún
    combo al 100% (no debería pasar: la entrada única siempre llena) se cae
    al comportamiento anterior con nota honesta.

    SUPERVIVENCIA > NET: sobrevive = ningún trade pierde más del umbral de
    alarma (% de la cuenta) Y el max DD no se come la cuenta entera. Entre
    supervivientes: el combo más SIMPLE (menos palancas), luego net. Si
    nada sobrevive, se recomienda IGUAL el combo del 100% que más acerca
    (mínimo peor trade % de la cuenta) — aunque sea net-negativo, con el
    costo a la vista."""
    cu = float(cuenta_usd)
    umbral = prot.get("umbral_alarma_pct", ALARMA_PCT)

    def pct(v) -> float | None:
        return round(100 * abs(v or 0.0) / cu, 1) if cu > 0 else None

    alarmas = [dict(p, pct_cuenta=pct(p["pnl_usd"]))
               for p in prot.get("perdedores") or []
               if -p["pnl_usd"] >= umbral / 100.0 * cu]
    crudo = {
        "net_usd": crudo_total.get("net_usd"),
        "pf": crudo_total.get("pf"),
        "wr_pct": crudo_total.get("wr_pct"),
        "max_dd_usd": crudo_total.get("max_dd_usd"),
        "peor_trade_usd": crudo_total.get("peor_trade_usd"),
        "participacion_pct": 100.0,
        "peor_pct_cuenta": pct(min(crudo_total.get("peor_trade_usd") or 0.0,
                                   0.0)),
        "dd_pct_cuenta": pct(crudo_total.get("max_dd_usd")),
    }
    out = {"cuenta_usd": cu, "umbral_alarma_pct": umbral,
           "alarmas": alarmas, "n_alarmas": len(alarmas),
           "crudo": crudo,
           "etiqueta": prot.get("etiqueta") or ETIQUETA_PROTECCION,
           "elegido": None, "protegido": False, "efecto": None,
           "nota_supervivencia": None}
    combos = prot.get("combos") or []
    if not combos:
        return out

    def peor_pct(c: dict) -> float:
        return pct(min(c["metricas"].get("peor_trade_usd") or 0.0, 0.0))

    def dd_pct(c: dict) -> float:
        return pct(c["metricas"].get("max_dd_usd")) or 0.0

    # R-obs-2 — elegibles: SOLO participación 100% (capar pérdidas sin
    # saltar trades). 99.95 por redondeo del motor (round a 1 decimal).
    plenos = [c for c in combos
              if (c.get("participacion_pct") or 0.0) >= 99.95]
    candidatos = plenos or combos          # fallback honesto (no debería)
    supervivientes = [c for c in candidatos
                      if peor_pct(c) <= umbral and dd_pct(c) < 100.0]
    if supervivientes:
        elegido = max(supervivientes,
                      key=lambda c: (-c["n_palancas"],
                                     c["metricas"].get("net_usd") or -9e18))
        protegido = True
    else:
        elegido = min(candidatos,
                      key=lambda c: (peor_pct(c),
                                     -(c["metricas"].get("net_usd")
                                       or -9e18)))
        protegido = False
    m = elegido["metricas"]
    out.update({
        "elegido": elegido,
        "protegido": protegido,
        "efecto": {
            "peor_trade_usd": m.get("peor_trade_usd"),
            "peor_pct_cuenta": peor_pct(elegido),
            "max_dd_usd": m.get("max_dd_usd"),
            "dd_pct_cuenta": dd_pct(elegido),
            "costo_net_usd": round((m.get("net_usd") or 0.0)
                                   - (crudo_total.get("net_usd") or 0.0), 2),
            "participacion_pct": elegido["participacion_pct"],
            "ganadoras_cortadas_pct": elegido["ganadoras_cortadas_pct"],
        },
        "nota_supervivencia": (
            "participación 100% obligatoria (capar pérdidas SIN saltar "
            "trades) · supervivencia > net: la protección se recomienda "
            "aunque cueste net"
            + ("" if plenos else
               " · ⚠ ningún combo participa al 100% — fallback al barrido "
               "completo")
            + ("" if protegido else
               f" — ningún combo deja el peor trade ≤ {umbral:.0f}% de la "
               f"cuenta; se muestra el que más se acerca")),
    })
    return out


# ---------------------------------------------------------------------------
# 6. Reconciliación fills escalera ↔ pullback del Lab
# ---------------------------------------------------------------------------

def reconcile_fills(sts: list[SimTrade],
                    lab_fill_rates: dict[float, float | None]) -> dict:
    """Escalera (MAE, límite trabajando todo el trade) vs pullback del Lab
    (ventana 180 min desde la señal, puede llenar tras la salida). Deben
    coincidir en los niveles someros; en los profundos la ventana corta
    fills tardíos (Δ>0) — el costo real de un cancel_after corto."""
    if not sts:
        return {"niveles": [], "max_delta_somero_pp": None}
    filas = []
    for lvl in sorted(lab_fill_rates):
        lab = lab_fill_rates[lvl]
        mae = round(100 * sum(1 for st in sts if st.mae_atr >= lvl)
                    / len(sts), 1)
        filas.append({
            "nivel_atr": lvl,
            "fill_mae_pct": mae,             # escalera (todo el trade)
            "fill_lab_pct": lab,             # pullback Lab (ventana 180m)
            "delta_pp": (round(mae - lab, 1) if lab is not None else None),
        })
    someros = [f for f in filas
               if f["nivel_atr"] <= 2.0 and f["delta_pp"] is not None]
    max_delta = (max(abs(f["delta_pp"]) for f in someros)
                 if someros else None)
    return {"niveles": filas, "max_delta_somero_pp": max_delta}


# ---------------------------------------------------------------------------
# 7. Gating automático (SPEC §5 + Anexo 25: nunca elegir por in-sample)
# ---------------------------------------------------------------------------

def _score(m: dict) -> float | None:
    """Score = net / maxDD (PnL + control de pérdidas, la razón PnL/DD de la
    referencia). DD=0 con net>0 → inf."""
    if m.get("n", 0) == 0 or m.get("net_usd") is None:
        return None
    dd = m.get("max_dd_usd") or 0.0
    if dd == 0:
        return math.inf if m["net_usd"] > 0 else 0.0
    return m["net_usd"] / dd


def gate_config(cfg: dict, base: dict) -> dict:
    """Gating SPEC §5: supera la base en el SCORE (net/maxDD — PnL + control
    de pérdidas; una config de riesgo puede ceder net a cambio de DD) Y
    sobrevive OOS (ΔPF out > 0; PF_out None = sin pérdidas OOS = sobrevive).
    Peor que la base en score Y en net → "descartado – no aporta"."""
    d_net = cfg["total"]["net_usd"] - base["total"]["net_usd"]
    s, sb = _score(cfg["total"]), _score(base["total"])
    supera = s is not None and sb is not None and s > sb
    pf_out = cfg["out"].get("pf")
    pf_out_base = base["out"].get("pf")
    sobrevive = (pf_out is None
                 or pf_out_base is None
                 or pf_out > pf_out_base)
    if not supera and d_net <= 0:
        estado = "descartado – no aporta"
    elif supera and sobrevive:
        estado = "aprobada"
    elif supera:
        estado = "no sobrevive OOS"
    else:
        estado = "no supera la base (score)"
    def fmt(v):                      # inf (DD=0) no es JSON válido → None
        return round(v, 2) if v is not None and math.isfinite(v) else None

    return {
        "estado": estado,
        "delta_net_usd": round(d_net, 2),
        "score": fmt(_score(cfg["total"])),
        "score_base": fmt(_score(base["total"])),
        "flags": (["low_n_out"] if cfg.get("low_n_out") else [])
                 + (["informativo"] if "informativo" in cfg.get("etiquetas", [])
                    else []),
    }


# ---------------------------------------------------------------------------
# MR-3 — Robustez walk-forward + estrés de la pierna profunda
# ---------------------------------------------------------------------------

# SPEC §9.4: mitades del walk-forward con menos de ~40 trades → frágil.
FRAGIL_HALF_N = 40


def _wf_blocks(sts: list[SimTrade]) -> dict[str, list[int]]:
    """Bloques del walk-forward: in/out (split 70/30 del Lab) + mitades
    temporales H1/H2 (estabilidad 'ambas mitades' de la referencia §4)."""
    half = len(sts) // 2
    return {
        "in": [i for i, st in enumerate(sts) if st.in_sample],
        "out": [i for i, st in enumerate(sts) if not st.in_sample],
        "h1": list(range(half)),
        "h2": list(range(half, len(sts))),
    }


def walk_forward_config(sts: list[SimTrade], cfg_outcomes: list,
                        base_outcomes: list) -> dict:
    """Robustez por bloque (ΔPF vs la base del MISMO bloque). El número que
    manda es OOS; H1/H2 responden '¿aguanta ambas mitades?'. Nunca se
    concluye por in-sample."""
    blocks = _wf_blocks(sts)
    out: dict = {"bloques": {}}
    for name, idxs in blocks.items():
        m = metrics_usd([cfg_outcomes[i][1] for i in idxs])
        mb = metrics_usd([base_outcomes[i][1] for i in idxs])
        n_part = sum(1 for i in idxs if cfg_outcomes[i][2])
        d_pf = (round(m["pf"] - mb["pf"], 2)
                if m.get("pf") is not None and mb.get("pf") is not None
                else None)
        flags = []
        if n_part < LOW_N_OUT:
            flags.append("n_bajo")
        # SPEC §9.4: MITADES del walk-forward con < ~40 trades → frágil
        # (el bloque OOS es 30% por diseño; su tamaño ya está en `n`).
        if name in ("h1", "h2") and len(idxs) < FRAGIL_HALF_N:
            flags.append("robustez_fragil")
        out["bloques"][name] = {
            "n": len(idxs), "n_participados": n_part,
            "pf": m.get("pf"), "pf_base": mb.get("pf"), "delta_pf": d_pf,
            "net_usd": m.get("net_usd"), "max_dd_usd": m.get("max_dd_usd"),
            "flags": flags,
        }
    # Veredicto (semántica de la referencia §4): supera la base FUERA DE
    # MUESTRA y es RENTABLE en ambas mitades (estable, no colapsa). NO se
    # exige ΔPF>0 por mitad: un backstop cede PF en la mitad afortunada por
    # construcción (el seguro se paga cuando no hace falta) — ese Δ queda a
    # la vista en los bloques, pero la decisión es OOS.
    dp_out = out["bloques"]["out"]["delta_pf"]
    pf_h1 = out["bloques"]["h1"]["pf"]
    pf_h2 = out["bloques"]["h2"]["pf"]
    flags_all = sorted({f for b in out["bloques"].values()
                        for f in b["flags"]})
    if dp_out is None or pf_h1 is None or pf_h2 is None:
        out["veredicto"] = "sin datos comparables"
    elif dp_out > 0 and pf_h1 >= 1.0 and pf_h2 >= 1.0:
        out["veredicto"] = ("validado" if not flags_all
                            else "validado (con banderas)")
    elif dp_out > 0:
        out["veredicto"] = "mixto — pierde en una mitad"
    else:
        out["veredicto"] = "no generaliza OOS"
    out["flags"] = flags_all
    return out


def deep_leg_stress(sts: list[SimTrade], legs: tuple,
                    backstop_usd: float | None, ppt: float,
                    hc: HaircutCfg | None = None,
                    tp_by_side: dict | None = None,
                    cancel_after_s: float | None = None) -> dict:
    """Estrés de la pierna MÁS PROFUNDA de una config: ¿cuántos trades la
    llenan (por bloque)?, ¿su contribución son pocos aciertos afortunados?,
    ¿el PF aguanta SIN ella (contrafactual: nunca llena)?"""
    hc = hc or HaircutCfg()
    b_pts = backstop_usd / ppt if backstop_usd else None
    d_max, w_max = max(legs, key=lambda lw: lw[0])
    blocks = _wf_blocks(sts)

    fills_idx = [i for i, st in enumerate(sts)
                 if leg_filled(st, d_max, cancel_after_s)[0]]
    contrib: list[float] = []
    for i in fills_idx:
        st = sts[i]
        stopped = b_pts is not None and st.mae_pts >= b_pts
        tp_atr = (tp_by_side or {}).get(st.side)
        tp_hit = (not stopped and tp_atr is not None
                  and st.mfe_atr >= tp_atr)
        if stopped:
            pnl_pts = -(b_pts + hc.gap_pts - d_max * st.atr_pts)
        elif tp_hit:
            pnl_pts = (tp_atr + d_max) * st.atr_pts
        else:
            pnl_pts = st.native_pnl_pts(ppt) + d_max * st.atr_pts
        contrib.append(w_max * (pnl_pts - hc.slip_pts) * ppt)

    con = config_outcomes(sts, legs, b_pts, tp_by_side, ppt, hc,
                          cancel_after_s=cancel_after_s)
    sin = config_outcomes(sts, tuple((d, w) for d, w in legs if d < d_max),
                          b_pts, tp_by_side, ppt, hc,
                          cancel_after_s=cancel_after_s)
    pf_con_sin: dict = {}
    for name, idxs in blocks.items():
        m_con = metrics_usd([con[i][1] for i in idxs])
        m_sin = metrics_usd([sin[i][1] for i in idxs])
        pf_con_sin[name] = {"pf_con": m_con.get("pf"),
                            "pf_sin": m_sin.get("pf"),
                            "net_con": m_con.get("net_usd"),
                            "net_sin": m_sin.get("net_usd")}

    fills_set = set(fills_idx)
    wins = sum(1 for v in contrib if v > 0)
    return {
        "depth_atr": d_max,
        "micros": round(w_max * TOTAL_MICROS),
        "n_fills": len(fills_idx),
        "fills_por_bloque": {name: sum(1 for i in idxs if i in fills_set)
                             for name, idxs in blocks.items()},
        "contribucion": {
            "total_usd": round(sum(contrib), 2),
            "ganadores": wins,
            "perdedores": len(contrib) - wins,
            "mediana_usd": (round(median(contrib), 2) if contrib else None),
            "peor_usd": round(min(contrib), 2) if contrib else None,
            "mejor_usd": round(max(contrib), 2) if contrib else None,
        },
        "pf_por_bloque_con_vs_sin": pf_con_sin,
        "flags": (["n_bajo_fills"]
                  if len(fills_idx) < LOW_N_OUT else []),
    }


def robustez_study(sts: list[SimTrade], configs: list[dict], ppt: float,
                   hc: HaircutCfg | None = None,
                   cancel_after_s: float | None = None,
                   tope_natural_atr: float | None = None) -> dict:
    """Walk-forward sobre las configs candidatas + head-to-head de los DOS
    líderes del barrido (por net y por score) + estrés de la pierna profunda
    del líder por score. Elegido = máximo score entre los VALIDADOS por el
    walk-forward (nunca por in-sample) CUYAS piernas respetan el tope
    natural del corte de fills — una pierna más honda que el tope llena
    <10% de las veces o pierde >50% de sus fills al cancel_after: no es una
    pierna de producción, es lotería de régimen. Sin ninguno dentro del
    tope, cae al mejor validado global con bandera."""
    hc = hc or HaircutCfg()
    base_out = config_outcomes(sts, SENAL, None, None, ppt, hc)

    def outcomes_de(cfg: dict) -> list:
        legs = tuple((l["depth_atr"], l["peso"]) for l in cfg["legs"])
        b_pts = (cfg["backstop_usd"] / ppt if cfg["backstop_usd"] else None)
        return config_outcomes(sts, legs, b_pts, cfg["tp_por_lado_atr"],
                               ppt, hc, cfg["solo_lado"], cancel_after_s)

    utiles = [c for c in configs if "informativo" not in c["etiquetas"]
              and not c["solo_lado"]]
    barrido = [c for c in utiles if "barrido" in c["etiquetas"]]
    lider_net = (max(barrido, key=lambda c: c["total"]["net_usd"])
                 if barrido else None)
    lider_score = (max(barrido, key=lambda c: c["gate"]["score"] or -9e18)
                   if barrido else None)

    candidatos: list[dict] = []
    vistos: set[str] = set()

    def push(c: dict | None) -> None:
        if c is not None and c["nombre"] not in vistos:
            vistos.add(c["nombre"])
            candidatos.append(c)

    push(lider_score)
    push(lider_net)
    for c in sorted(utiles, key=lambda c: -(c["gate"]["score"] or -9e18))[:6]:
        push(c)
    for c in sorted(utiles, key=lambda c: -c["total"]["net_usd"])[:3]:
        push(c)
    for c in utiles:
        if "referencia" in c["etiquetas"] or c["nombre"].startswith("señal"):
            push(c)

    wf_por_nombre: dict[str, dict] = {}
    tabla = []
    for c in candidatos:
        wf = walk_forward_config(sts, outcomes_de(c), base_out)
        wf_por_nombre[c["nombre"]] = wf
        tabla.append({"nombre": c["nombre"],
                      "participacion_pct": c["participacion_pct"],
                      "score": c["gate"]["score"], **wf})

    head_to_head = None
    if (lider_net and lider_score
            and lider_net["nombre"] != lider_score["nombre"]):
        head_to_head = {
            "lider_net": {"nombre": lider_net["nombre"],
                          **wf_por_nombre[lider_net["nombre"]]},
            "lider_score": {"nombre": lider_score["nombre"],
                            **wf_por_nombre[lider_score["nombre"]]},
        }

    estres = None
    if lider_score:
        legs = tuple((l["depth_atr"], l["peso"]) for l in lider_score["legs"])
        if max(d for d, _ in legs) >= 4.0:
            estres = deep_leg_stress(sts, legs, lider_score["backstop_usd"],
                                     ppt, hc, lider_score["tp_por_lado_atr"],
                                     cancel_after_s)
            estres["config"] = lider_score["nombre"]

    validados = [t for t in tabla if t["veredicto"].startswith("validado")]
    por_nombre = {c["nombre"]: c for c in candidatos}

    def _max_depth(nombre: str) -> float:
        return max((l["depth_atr"]
                    for l in por_nombre[nombre]["legs"] if l["peso"] > 0),
                   default=0.0)

    elegido = None
    if validados:
        dentro_tope = ([t for t in validados
                        if _max_depth(t["nombre"]) <= tope_natural_atr]
                       if tope_natural_atr is not None else validados)
        pool = dentro_tope or validados
        mejor = max(pool,
                    key=lambda t: por_nombre[t["nombre"]]["gate"]["score"]
                    or -9e18)
        elegido = {"nombre": mejor["nombre"],
                   "config": por_nombre[mejor["nombre"]],
                   "walk_forward": wf_por_nombre[mejor["nombre"]]}
        if tope_natural_atr is not None and not dentro_tope:
            elegido["flag_tope"] = ("excede_tope_natural — ningún validado "
                                    "dentro del tope; revisar")
        elif (tope_natural_atr is not None
              and _max_depth(mejor["nombre"]) <= tope_natural_atr):
            elegido["tope_natural_atr"] = tope_natural_atr
    return {
        "tabla": tabla,
        "head_to_head": head_to_head,
        "estres_pierna_profunda": estres,
        "elegido": elegido,
        "nota": ("veredicto = supera la base FUERA DE MUESTRA (ΔPF out > 0) "
                 "y rentable en AMBAS mitades (PF ≥ 1, estable); el ΔPF por "
                 "mitad queda a la vista — el backstop cede PF en la mitad "
                 "afortunada por construcción (el precio del seguro). "
                 "Elegido = máximo score (net/maxDD) entre validados — "
                 "nunca por in-sample"),
    }


# ---------------------------------------------------------------------------
# Orquestador MR-2
# ---------------------------------------------------------------------------

def run_studies(sts: list[SimTrade], ppt: float,
                hc: HaircutCfg | None = None,
                lab_fill_rates: dict[float, float | None] | None = None,
                cancel_after_s: float | None = CANCEL_AFTER_MAX_S,
                listado_crudo: dict | None = None,
                ) -> dict:
    """Corre TODOS los estudios MR-2 y devuelve el dict completo (lo persiste
    nt_riesgo.calcular en runs/estudios_<fecha>.json; MR-3 lo convierte en
    reporte + heatmap + recomendación).

    cancel_after_s (default 3600 = máximo duro de TradersPost): el barrido
    PRINCIPAL corre con el corte de tiempo de llenado — los fills realistas
    de producción. None = modelo original sin corte (solo para estudio).

    listado_crudo (opcional, lo arma el caller con los Trade completos):
    métricas del ListadoDeOperaciones CRUDO sin filtro de universo ATR +
    duración media de ganadores/perdedores — se persiste tal cual."""
    hc = hc or HaircutCfg()
    if cancel_after_s is not None:
        cancel_after_s = min(float(cancel_after_s), CANCEL_AFTER_MAX_S)
    ca = cancel_after_s
    base_cfg = eval_config(sts, "LÍNEA BASE (señal, sin nada)", SENAL,
                           None, ppt, None, hc)
    mae_floor = mae_floor_study(sts, ppt, hc)
    backstop = backstop_sweep(sts, ppt, hc)
    b_opt = (backstop["optimo"]["backstop_usd"]
             if backstop["optimo"] else None)
    # TP-meta se busca sobre el stack balanceada+backstop (como la
    # referencia): el TP interactúa con las piernas, no con la señal sola.
    tp = tp_nominal_study(sts, ppt, hc, meta_legs=BALANCEADA,
                          meta_b_usd=b_opt, cancel_after_s=ca)
    tp_nominal = tp["tp_nominal_atr"] or None
    tp_meta = ({"long": tp["tp_meta_mejor"]["tp_long"],
                "short": tp["tp_meta_mejor"]["tp_short"]}
               if tp["tp_meta_mejor"] else None)
    configs = (build_configs(sts, ppt, b_opt, tp_nominal, tp_meta, hc, ca)
               if b_opt else [])
    for cfg in configs:
        cfg["gate"] = gate_config(cfg, base_cfg)
    configs.sort(key=lambda c: c["total"].get("net_usd") or -9e18,
                 reverse=True)

    # Corte de fills + comparativa contra el modelo sin corte (honestidad:
    # los números con corte son más bajos — son los de producción).
    corte_fills = fills_cutoff_study(sts, ca) if ca is not None else None
    comparativa_sin_corte = None
    if ca is not None and b_opt:
        sin = build_configs(sts, ppt, b_opt, tp_nominal, tp_meta, hc, None)
        for cfg in sin:
            cfg["gate"] = gate_config(cfg, base_cfg)
        sin.sort(key=lambda c: c["total"].get("net_usd") or -9e18,
                 reverse=True)
        utiles_sin = [c for c in sin if "informativo" not in c["etiquetas"]
                      and not c["solo_lado"]]
        comparativa_sin_corte = {
            "nota": ("el modelo original ('alguna vez llena') — SOLO para "
                     "comparar; la recomendación sale del barrido CON corte"),
            "top_net": [{
                "nombre": c["nombre"],
                "net_usd": c["total"].get("net_usd"),
                "pf": c["total"].get("pf"),
                "max_dd_usd": c["total"].get("max_dd_usd"),
                "participacion_pct": c["participacion_pct"],
                "score": c["gate"]["score"],
            } for c in utiles_sin[:5]],
            "lider_score_sin_corte": (max(
                utiles_sin, key=lambda c: c["gate"]["score"] or -9e18)
                ["nombre"] if utiles_sin else None),
        }

    # ── MR-3: robustez walk-forward + recomendación (número OOS manda) ──
    ls = ls_asymmetry(sts)
    # P1b — la 4ª palanca: gestión por lado, ESTRUCTURAL (no OOS)
    gestion_lado = side_management(sts, ls)
    robustez = (robustez_study(
        sts, configs, ppt, hc, ca,
        corte_fills["tope_natural_atr"] if corte_fills else None)
        if configs else None)

    # ── R-obs-1: Protección de cuenta = espejo COMPLETO del estudio
    # validado sobre TODA la muestra. Las candidatas de escalera son las
    # MISMAS que el walk-forward puso sobre la mesa (líderes + top score +
    # referencias) + entrada única + balanceada — mismas palancas, mismas
    # métricas; solo cambia split/gate (aquí decide supervivencia > net). ──
    esc_cands: list[tuple[str, tuple]] = list(PROTECCION_ESCALERAS_BASE)
    if robustez:
        por_nombre = {c["nombre"]: c for c in configs}
        vistas = {legs for _, legs in esc_cands}
        for t in robustez["tabla"]:
            c = por_nombre.get(t["nombre"])
            if c is None or c["solo_lado"]:
                continue
            legs = tuple((l["depth_atr"], l["peso"]) for l in c["legs"])
            if legs not in vistas:
                vistas.add(legs)
                esc_cands.append((c["nombre"], legs))
    proteccion = proteccion_study(
        sts, ppt, hc,
        suelo_atr=mae_floor["ganadoras_mae_atr"]["p95"],
        tp_nominal=tp_nominal, gestion_lado=gestion_lado,
        escaleras=esc_cands, cancel_after_s=ca)

    recomendacion = None
    if robustez and robustez["elegido"]:
        el = robustez["elegido"]
        cfg = el["config"]
        wf_out = el["walk_forward"]["bloques"]["out"]
        recomendacion = {
            "config": cfg["nombre"],
            "escalera": {
                "anclaje": "precio_senal",
                "n_piernas": cfg["n_piernas"],
                "piernas": [{"depth_atr": l["depth_atr"],
                             "micros": round(l["peso"] * TOTAL_MICROS)}
                            for l in cfg["legs"]],
                "total_micros": TOTAL_MICROS,
            },
            # FIX-FX-BACKSTOP — pts en PRECISIÓN PLENA (usd/ppt): el motor offline
            # es instrumento-agnóstico (R-T9, sin tick) y el round(_,2) pensado
            # para índices aplastaba el backstop FX a 0.0 antes de aplicar. La
            # rejilla del tick la pone el despacho (sl_tp_calculator.round_to_tick
            # sobre el precio SL final); aquí NO se colapsa.
            "backstop": ({"usd_por_mini": b_opt,
                          "pts": b_opt / ppt,
                          "usd_por_micro": round(b_opt / TOTAL_MICROS, 2),
                          "tipo": "stop_precio_fijo_desde_senal"}
                         if b_opt else None),
            "tp_nominal_atr": tp_nominal,
            "confianza_oos": {
                "pf_out": wf_out["pf"],
                "delta_pf_out": wf_out["delta_pf"],
                "veredicto": el["walk_forward"]["veredicto"],
                "flags": el["walk_forward"]["flags"],
                "nota": "número OOS — el in-sample NUNCA decide",
            },
            "metricas": {"total": cfg["total"], "out": cfg["out"],
                         "participacion_pct": cfg["participacion_pct"]},
            "gestion_por_lado": ls.get("lectura"),
        }
        # cancel_after COHERENTE con el ladder elegido: p90 del toque de la
        # pierna más profunda incluida (MISMO estimador que el estudio vivo,
        # NX-17: min(3600, p90·60+60)) — lo que hay que poner en
        # entry_reserve_timeout_seconds para que las piernas del elegido
        # alcancen a llenar dentro del máximo duro de TradersPost.
        d_max = max((l["depth_atr"] for l in cfg["legs"]
                     if l["peso"] > 0 and l["depth_atr"] > 0), default=None)
        cancel_coherente = None
        if d_max is not None:
            touches = [st.pb_touch_min[str(float(d_max))] for st in sts
                       if st.pb_touch_min is not None
                       and str(float(d_max)) in st.pb_touch_min]
            cancel_coherente = suggest_cancel_after(touches)
        recomendacion["cancel_after_seconds"] = cancel_coherente
        if ca is not None and corte_fills:
            recomendacion["corte"] = {
                "cancel_after_s_estudio": ca,
                "tope_natural_atr": corte_fills["tope_natural_atr"],
                "nota": ("métricas CON corte de fills = las reales de "
                         "producción (más bajas que sin corte, a propósito)"),
            }

    return {
        "universo": {"n": len(sts),
                     "n_atr_estimado": sum(1 for st in sts
                                           if st.atr_estimado)},
        "haircut": {"comision_rt_usd": hc.comision_rt_usd,
                    "slip_pts": hc.slip_pts, "gap_pts": hc.gap_pts},
        "linea_base": base_cfg,
        "mae_floor": mae_floor,
        "backstop": backstop,
        "tp": tp,
        "ls": ls,
        "gestion_lado": gestion_lado,
        "proteccion": proteccion,
        "listado_crudo": listado_crudo,
        "configs": configs,
        "corte_fills": corte_fills,
        "comparativa_sin_corte": comparativa_sin_corte,
        "robustez": robustez,
        "recomendacion": recomendacion,
        "reconciliacion_fills": (reconcile_fills(sts, lab_fill_rates)
                                 if lab_fill_rates else None),
        "descartados_por_diseno": [
            "SL duro ×ATR (net-negativo — ver mae_floor)",
            "filtro de sesión/hora (no aporta)",
            "time-stop por duración (tautológico, redundante con backstop, "
            "choca con 'que cierre LuxAlgo' — validado 2026-07-04)",
        ],
    }
