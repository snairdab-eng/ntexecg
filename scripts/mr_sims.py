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

# Núcleo compartido (Directiva 1): agregación unit-agnóstica del Lab y el
# MISMO estimador de percentiles que el estudio de pullback vivo.
from app.services.lab_metrics import LOW_N_OUT, aggregate
from scripts.pullback_timing import pctl


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
        out.append(SimTrade(
            number=t.number, side=t.side, in_sample=t.in_sample,
            entry_price=t.entry_price, atr_pts=t.atr_entry,
            mae_pts=t.mae_pct / 100.0 * t.entry_price,
            mfe_pts=t.mfe_pct / 100.0 * t.entry_price,
            native_pnl_usd=t.pnl_usd,
            atr_estimado=t.number in est,
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


def ladder_outcome(st: SimTrade, legs: tuple, b_pts: float | None,
                   tp_atr_by_side: dict | None, ppt: float,
                   hc: HaircutCfg) -> tuple[float, float, bool]:
    """(pnl_usd, peso_llenado, ambigüedad pierna↔TP) de un trade.
    Fills por MAE (todo el trade); stop manda sobre TP (conservador)."""
    tp_atr = (tp_atr_by_side or {}).get(st.side)
    stopped = b_pts is not None and st.mae_pts >= b_pts
    tp_hit = (not stopped and tp_atr is not None and st.mfe_atr >= tp_atr)
    acc = filled_w = 0.0
    ambiguous = False
    for d, w in legs:
        if d > 0 and st.mae_atr < d:
            continue                       # la pierna nunca llenó
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
                    ) -> list[tuple[SimTrade, float, bool, bool]]:
    """Serie CRONOLÓGICA completa de una config: [(trade, usd, participó,
    ambiguo)] con 0.0 en lo no-participado — la única fuente para
    eval_config, el walk-forward y el estrés de piernas."""
    out: list[tuple[SimTrade, float, bool, bool]] = []
    for st in sts:
        if solo_lado and st.side != solo_lado:
            out.append((st, 0.0, False, False))
            continue
        usd, fw, amb = ladder_outcome(st, legs, b_pts, tp_by_side, ppt, hc)
        out.append((st, usd if fw > 0 else 0.0, fw > 0, amb))
    return out


def eval_config(sts: list[SimTrade], nombre: str, legs: tuple,
                backstop_usd: float | None, ppt: float,
                tp_by_side: dict | None = None,
                hc: HaircutCfg | None = None,
                solo_lado: str | None = None,
                etiquetas: tuple = ()) -> dict:
    """Evalúa una config sobre TODO el universo (no-participado = 0.0 para
    que net/DD sean comparables 1:1 contra la base en el mismo periodo).
    WR reportado = sobre trades PARTICIPADOS (documentado)."""
    hc = hc or HaircutCfg()
    b_pts = backstop_usd / ppt if backstop_usd else None
    outcomes = config_outcomes(sts, legs, b_pts, tp_by_side, ppt, hc,
                               solo_lado)
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
                  hc: HaircutCfg | None = None) -> list[dict]:
    """La parrilla de configs del estudio. Alta participación de primera
    clase (la 60/40 la DECIDE el estudio — Directiva 3.1); TP-meta marcado
    informativo (la recomendación honra 'que cierre LuxAlgo')."""
    hc = hc or HaircutCfg()
    cfgs: list[dict] = []

    def add(nombre, legs, b=backstop_usd, tp=None, lado=None, tags=()):
        cfgs.append(eval_config(sts, nombre, legs, b, ppt, tp, hc,
                                lado, tags))

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


# ---------------------------------------------------------------------------
# 4. TP nominal por ENCIMA del cierre de LuxAlgo (+ TP-meta informativo)
# ---------------------------------------------------------------------------

TP_META_GRID_L = (4.0, 4.5, 5.0, 5.5, 6.0, 6.5)
TP_META_GRID_S = (0.5, 1.0, 1.5, 2.0)


def _ceil_half(x: float) -> float:
    return math.ceil(x * 2.0) / 2.0


def tp_nominal_study(sts: list[SimTrade], ppt: float,
                     hc: HaircutCfg | None = None,
                     meta_legs: tuple = SENAL,
                     meta_b_usd: float | None = None) -> dict:
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
        if exc:
            p95, p99 = pctl(exc, 0.95), pctl(exc, 0.99)
            tp = _ceil_half(p99)
            if tp <= p99:
                tp += 0.5                  # estrictamente POR ENCIMA del p99
            tp_nominal[lado] = tp
            dispararia = sum(1 for st in del_lado if st.mfe_atr >= tp)
        else:
            p95 = p99 = None
            dispararia = 0
        giveback = sum(st.mfe_pts * ppt - st.native_pnl_usd
                       for st in ganadoras)
        por_lado[lado] = {
            "n_ganadoras": len(ganadoras),
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
                            meta_b_usd, ppt, {"long": L, "short": S}, hc)
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
                    tp_by_side: dict | None = None) -> dict:
    """Estrés de la pierna MÁS PROFUNDA de una config: ¿cuántos trades la
    llenan (por bloque)?, ¿su contribución son pocos aciertos afortunados?,
    ¿el PF aguanta SIN ella (contrafactual: nunca llena)?"""
    hc = hc or HaircutCfg()
    b_pts = backstop_usd / ppt if backstop_usd else None
    d_max, w_max = max(legs, key=lambda lw: lw[0])
    blocks = _wf_blocks(sts)

    fills_idx = [i for i, st in enumerate(sts) if st.mae_atr >= d_max]
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

    con = config_outcomes(sts, legs, b_pts, tp_by_side, ppt, hc)
    sin = config_outcomes(sts, tuple((d, w) for d, w in legs if d < d_max),
                          b_pts, tp_by_side, ppt, hc)
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
                   hc: HaircutCfg | None = None) -> dict:
    """Walk-forward sobre las configs candidatas + head-to-head de los DOS
    líderes del barrido (por net y por score) + estrés de la pierna profunda
    del líder por score. Elegido = máximo score entre los VALIDADOS por el
    walk-forward (nunca por in-sample)."""
    hc = hc or HaircutCfg()
    base_out = config_outcomes(sts, SENAL, None, None, ppt, hc)

    def outcomes_de(cfg: dict) -> list:
        legs = tuple((l["depth_atr"], l["peso"]) for l in cfg["legs"])
        b_pts = (cfg["backstop_usd"] / ppt if cfg["backstop_usd"] else None)
        return config_outcomes(sts, legs, b_pts, cfg["tp_por_lado_atr"],
                               ppt, hc, cfg["solo_lado"])

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
                                     ppt, hc, lider_score["tp_por_lado_atr"])
            estres["config"] = lider_score["nombre"]

    validados = [t for t in tabla if t["veredicto"].startswith("validado")]
    por_nombre = {c["nombre"]: c for c in candidatos}
    elegido = None
    if validados:
        mejor = max(validados,
                    key=lambda t: por_nombre[t["nombre"]]["gate"]["score"]
                    or -9e18)
        elegido = {"nombre": mejor["nombre"],
                   "config": por_nombre[mejor["nombre"]],
                   "walk_forward": wf_por_nombre[mejor["nombre"]]}
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
                ) -> dict:
    """Corre TODOS los estudios MR-2 y devuelve el dict completo (lo persiste
    nt_riesgo.calcular en runs/estudios_<fecha>.json; MR-3 lo convierte en
    reporte + heatmap + recomendación)."""
    hc = hc or HaircutCfg()
    base_cfg = eval_config(sts, "LÍNEA BASE (señal, sin nada)", SENAL,
                           None, ppt, None, hc)
    mae_floor = mae_floor_study(sts, ppt, hc)
    backstop = backstop_sweep(sts, ppt, hc)
    b_opt = (backstop["optimo"]["backstop_usd"]
             if backstop["optimo"] else None)
    # TP-meta se busca sobre el stack balanceada+backstop (como la
    # referencia): el TP interactúa con las piernas, no con la señal sola.
    tp = tp_nominal_study(sts, ppt, hc, meta_legs=BALANCEADA,
                          meta_b_usd=b_opt)
    tp_nominal = tp["tp_nominal_atr"] or None
    tp_meta = ({"long": tp["tp_meta_mejor"]["tp_long"],
                "short": tp["tp_meta_mejor"]["tp_short"]}
               if tp["tp_meta_mejor"] else None)
    configs = (build_configs(sts, ppt, b_opt, tp_nominal, tp_meta, hc)
               if b_opt else [])
    for cfg in configs:
        cfg["gate"] = gate_config(cfg, base_cfg)
    configs.sort(key=lambda c: c["total"].get("net_usd") or -9e18,
                 reverse=True)

    # ── MR-3: robustez walk-forward + recomendación (número OOS manda) ──
    ls = ls_asymmetry(sts)
    robustez = robustez_study(sts, configs, ppt, hc) if configs else None
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
            "backstop": ({"usd_por_mini": b_opt,
                          "pts": round(b_opt / ppt, 2),
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
        "configs": configs,
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
