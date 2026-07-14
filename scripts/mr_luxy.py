#!/usr/bin/env python3
"""mr_luxy — LOTE L2: estudio Luxy (Riesgo v2) SOBRE el motor real.

Módulo NUEVO (no extiende mr_sims) — justificación:
  · Cero duplicación: REUSA las primitivas del motor (metrics_usd, from_trades,
    leg_filled, backstop_sweep, mae_floor_study, tp_nominal_study,
    side_management) y del Lab (split_in_out, touch_minutes). No copia ninguna.
  · La única palanca NUEVA (BREAKEVEN) vive aislada aquí → el arquitecto revisa
    un solo archivo, y la superficie pública de mr_sims (y sus patch-targets de
    test, `recrear` bit-a-bit de v1) queda intacta.

Tres conjuntos (SPEC A.1/P1), con las MISMAS primitivas:
  CRUDO      = señal sin palancas, TODA la muestra (metrics_usd sobre pnl nativo).
  IN-SAMPLE  = con las palancas derivadas del in-sample, TODA la muestra.
  OOS        = con las palancas derivadas del in-sample, SOLO la porción reciente
               apartada (el semáforo — gate discipline R-T10).

Disciplina OOS (R-T10): `derive_levers` toma SOLO los trades de su ventana. La
fila in-sample de la Tabla B se deriva con in-sample; la fila OOS con OOS; ninguna
mira la otra. Split por tiempo COMPARTIDO con el motor (`split_in_out`).

Requisitos innegociables:
  R-T1  fills de escalera vía `mr_sims.leg_filled` con cancel_after (≤3600s).
  R-T2  intrabar SOLO del master enriched: el BE se resuelve con los tiempos de
        primer toque del walk B4.0 del Lab (`touch_minutes`), no con ruta propia.
  R-T3  BREAKEVEN con convención intrabar PESIMISTA — ver `_luxy_exit_atr`.
  R-T4  TP estilo p99 de cierres por lado (`tp_nominal_study`).
  R-T5  el barrido de SL respeta el suelo = MAE p95 de GANADORAS (`mae_floor_study`).
  R-T6  lado derivado in-sample (`side_management`), diagnóstico.
  R-T9  usd_por_punto del master (el caller lo pasa; jamás del CSV).

Master DEGRADADO (sin HOLC/intrabar): estudio LIMITADO honesto — crudo + lo que el
export permite; las palancas que exigen intrabar (fills con corte, BE) quedan
'no disponibles sin HOLC'. Nada finge.
"""
from __future__ import annotations

import math

from scripts.mr_sims import (
    BALANCEADA,
    CANCEL_AFTER_MAX_S,
    HaircutCfg,
    SimTrade,
    backstop_sweep,
    from_trades,
    leg_filled,
    ladder_outcome,
    mae_floor_study,
    metrics_usd,
    side_management,
    tp_nominal_study,
)
from scripts.pullback_timing import pctl

TOTAL_MICROS = 10

# Rangos de resolución del exit — PEOR primero (empate de minuto → gana el más
# dañino para la posición). SL (−) < BE (0) < TP (+).
_RANK_STOP, _RANK_BE, _RANK_TP = 0, 1, 2
_REASON = {_RANK_STOP: "stop", _RANK_BE: "breakeven", _RANK_TP: "tp"}

# R-T7 — UNA sola partición de sesiones/zonas horarias (en tiempo de New York):
# la FUENTE canónica es `scripts.sesiones_et` (extraída en L7a). Luxy la CONSUME;
# el estudio construye `reco.zones`/`zones_partition` de aquí y el front las
# renderiza tal cual (front == motor, no re-particiona). Se re-exportan para no
# romper los `mrl.LUXY_ZONES` / `mrl.zone_of_hour` de callers y tests.
from scripts.sesiones_et import LUXY_ZONES, _DAY_ES, zone_of_hour  # noqa: E402,F401


# ---------------------------------------------------------------------------
# Reparto de la escalera (WHAT del andamio; el CÓMO se construye sobre el motor)
# ---------------------------------------------------------------------------

# Reparto por MAYOR RESIDUO con C1≥1 — helper COMPARTIDO (no se duplica): vive
# en app/services/position_sizing (lo reusan el panel de Perfiles L4 y la regla
# 3 del Portafolio).
from app.services.position_sizing import alloc_from  # noqa: E402


def why_alloc(alloc: list[int], f2: float, f3: float) -> str:
    """Explicación humana del reparto (portada del andamio)."""
    a = alloc
    p2, p3 = round(f2 * 100), round(f3 * 100)
    if a[1] + a[2] == 0:
        prof = "prácticamente no hace pullbacks"
        tail = ("agregar por debajo casi nunca se ejecutaría, así que casi "
                "todo el tamaño entra en la señal (C1).")
    elif a[2] == 0:
        prof = "casi no hace pullbacks profundos"
        tail = ("C3 rara vez se llenaría, por eso queda en 0 y el peso se "
                "concentra en C1–C2.")
    elif a[0] > a[1] + a[2]:
        prof = "hace pullbacks moderados"
        tail = "la mayor parte del tamaño entra arriba y se agrega con mesura en C2/C3."
    else:
        prof = "hace pullbacks frecuentes y profundos"
        tail = ("conviene dejar bastante tamaño para C2 y C3, donde el precio "
                "sí suele darte fill.")
    return (f"El precio baja hasta C2 en el {p2}% de las operaciones y hasta "
            f"C3 en el {p3}%. Como esta estrategia {prof}, el estudio reparte "
            f"{a[0]}/{a[1]}/{a[2]}: {tail}")


def derive_ladder(win: list[SimTrade]) -> dict:
    """Escalera derivada de UNA ventana: niveles C2/C3 relativos al MFE típico
    (×ATR) y reparto por la FRECUENCIA real de pullback a cada nivel (f2/f3).
    Sin intrabar-corte todavía: los niveles/pesos salen de mae/mfe (siempre
    disponibles); el corte de fills se aplica al evaluar (R-T1)."""
    mfes = sorted(s.mfe_atr for s in win if s.mfe_atr > 0)
    if not mfes:
        return {"levels": [0.0], "alloc": [TOTAL_MICROS, 0, 0],
                "f2": 0.0, "f3": 0.0,
                "legs": ((0.0, 1.0),),
                "why_alloc": "sin MFE favorable — todo el tamaño en la señal."}
    med = pctl(mfes, 0.5)
    l2 = round(0.5 * med, 2)
    l3 = round(1.0 * med, 2)
    if l3 <= l2:
        l3 = round(l2 * 1.8, 2)
    n = len(win)
    f2 = sum(1 for s in win if s.mae_atr >= l2) / n if n else 0.0
    f3 = sum(1 for s in win if s.mae_atr >= l3) / n if n else 0.0
    alloc = alloc_from([1.0, f2, f3])
    legs = tuple((d, c / TOTAL_MICROS)
                 for d, c in zip((0.0, l2, l3), alloc) if c > 0)
    return {"levels": [0.0, l2, l3], "alloc": alloc, "f2": round(f2, 4),
            "f3": round(f3, 4), "legs": legs,
            "why_alloc": why_alloc(alloc, f2, f3)}


# ---------------------------------------------------------------------------
# EVALUADOR DEL EXIT — la palanca NUEVA (BREAKEVEN) con convención PESIMISTA
# ---------------------------------------------------------------------------

def _luxy_exit_atr(
    st: SimTrade, *,
    sl_atr: float | None, be_atr: float | None, tp_atr: float | None,
    t_sl: float | None, t_tp: float | None,
    be_return: tuple | None,
    native_close_atr: float,
) -> tuple[float, str]:
    """Exit de la posición en ×ATR desde la señal (favorable +, adverso −),
    convención intrabar **PESIMISTA** (R-T3).

    Minutos del walk B4.0 sancionado del motor — intrabar sin reconstrucción
    propia (R-T2):
      · `t_sl`  = toque del stop (adverso a sl×ATR). En producción None → 0.0
                  ("stop manda", pesimista, paridad con v1).
      · `t_tp`  = primer toque favorable a tp×ATR (`touch_minutes`).
      · `be_return` = `(minuto, tipo) | None` de `lab_analyze.be_return_minutes`.
                  None = nunca arma o nunca retorna tras armar → el BE NO
                  dispara. `tipo`:
                    - **"clean"**   retorno en barra posterior → evento BE normal.
                    - **"same_bar"** la barra de armado también vuelve a la
                      entrada: AMBIGUO (no se conoce el orden dentro de la barra).
                      Resolución pesimista PARA LA PALANCA: se computa el exit
                      SIN el evento BE y se aplica min(exit_sin_be, 0.0) — una
                      GANADORA ambigua queda recortada a 0 (motivo
                      "breakeven_ambiguo"; en producción esa barra ejecuta el
                      stop de BE), una PERDEDORA ambigua conserva su desenlace
                      (no se rescata).

    Resolución normal: Ex = evento MÁS TEMPRANO; en EMPATE de minuto (dos
    eventos en la MISMA barra salen con el mismo minuto) gana el PEOR: SL<BE<TP.
        · (i)  retorno a 0 ANTES de armar → `be_return` None → BE no dispara.
        · (ii) mismo minuto SL/BE (retorno clean) → gana SL.
        · (iii) mismo minuto BE/TP (retorno clean) → gana BE.
    TP sin minuto → ∞. Sin ningún evento → cierre NATIVO. Devuelve (Ex, motivo)."""
    armed = be_atr is not None and st.mfe_atr >= be_atr
    same_bar = (armed and be_return is not None and be_return[1] == "same_bar")

    events: list[tuple[float, int, float]] = []
    if sl_atr is not None and st.mae_atr >= sl_atr:
        events.append((0.0 if t_sl is None else t_sl, _RANK_STOP, -sl_atr))
    # SOLO el retorno LIMPIO entra como evento; el same_bar se resuelve aparte.
    if armed and be_return is not None and be_return[1] == "clean":
        events.append((be_return[0], _RANK_BE, 0.0))
    if tp_atr is not None and st.mfe_atr >= tp_atr:
        events.append((math.inf if t_tp is None else t_tp, _RANK_TP, tp_atr))

    if not events:
        ex_no_be, motivo = native_close_atr, "native"
    else:
        _t, rank, ex_no_be = min(events, key=lambda e: (e[0], e[1]))
        motivo = _REASON[rank]

    if same_bar:                        # ambiguo → pesimista para la palanca
        capped = min(ex_no_be, 0.0)
        return capped, ("breakeven_ambiguo" if capped == 0.0 else motivo)
    return ex_no_be, motivo


def luxy_outcome(
    st: SimTrade, fav: dict, be_ret: dict, *,
    legs: tuple, b_pts: float | None, tp_by_side: dict | None,
    be_atr: float | None, ppt: float, cancel_after_s: float | None,
) -> tuple[float, bool]:
    """(pnl_usd, participó) de un trade bajo las palancas Luxy. Fills de piernas
    vía `leg_filled` con corte (R-T1); exit COMÚN de la posición vía
    `_luxy_exit_atr`. `fav` = {tp_atr: minuto} (touch_minutes favorable);
    `be_ret` = {be_atr: minuto} (be_return_minutes). Cada pierna llenada a
    profundidad d gana (exit + d)×ATR."""
    sl_atr = (b_pts / st.atr_pts) if b_pts else None
    tp_atr = (tp_by_side or {}).get(st.side)
    ex, _motivo = _luxy_exit_atr(
        st, sl_atr=sl_atr, be_atr=be_atr, tp_atr=tp_atr,
        t_sl=None,                                   # producción: stop manda
        t_tp=(fav or {}).get(tp_atr) if tp_atr else None,
        be_return=(be_ret or {}).get(be_atr) if be_atr else None,
        native_close_atr=st.native_pnl_pts(ppt) / st.atr_pts)
    acc = 0.0
    filled_w = 0.0
    for d, w in legs:
        if not leg_filled(st, d, cancel_after_s)[0]:
            continue
        filled_w += w
        acc += w * (ex + d) * st.atr_pts
    usd = acc * ppt
    return (usd if filled_w > 0 else 0.0), filled_w > 0


# ---------------------------------------------------------------------------
# Toques del motor (touch_minutes) a los niveles del candidato — R-T2
# ---------------------------------------------------------------------------

def _touches_for(trade, tp_levels, be_triggers, keys5, idx5, bars5) -> tuple[dict, dict]:
    """(fav, be_ret) = {nivel_atr: minuto|None} para UN trade con el walk B4.0:
    `fav`  = primer toque favorable a cada nivel de TP (`touch_minutes`);
    `be_ret` = primer retorno a breakeven POSTERIOR al armado de cada trigger
               de BE (`be_return_minutes`, extensión aditiva del walk).
    Sin bars/keys (degradado) → ({}, {})."""
    from scripts.lab_analyze import be_return_minutes, touch_minutes
    if not keys5 or trade is None:
        return {}, {}
    fav = {}
    tp_levels = tuple(sorted({float(x) for x in tp_levels if x}))
    if tp_levels:
        _adv, fav_raw = touch_minutes(
            trade, keys5, idx5, bars5, adverse_lvls=(), favor_lvls=tp_levels)
        fav = {float(k): v for k, v in fav_raw.items()}
    be_ret = {}
    be_triggers = tuple(sorted({float(x) for x in be_triggers if x is not None}))
    if be_triggers:
        br = be_return_minutes(trade, keys5, idx5, bars5, be_triggers)
        be_ret = {float(k): v for k, v in br.items()}
    return fav, be_ret


# ---------------------------------------------------------------------------
# Derivación de palancas por VENTANA (R-T10: SOLO su ventana)
# ---------------------------------------------------------------------------

def _winners_mae_p95(win: list[SimTrade]) -> float | None:
    ws = sorted(s.mae_atr for s in win if s.native_pnl_usd > 0)
    return round(pctl(ws, 0.95), 2) if ws else None


def derive_levers(win: list[SimTrade], ppt: float, *,
                  cancel_after_s: float | None,
                  touches: dict | None,
                  has_intrabar: bool) -> dict:
    """Deriva TODAS las palancas de UNA ventana (in-sample u OOS,
    independientemente). `touches` = {number: (fav, adv)} del motor para el BE.
    """
    hc = HaircutCfg()
    suelo = _winners_mae_p95(win)                             # R-T5
    bs = backstop_sweep(win, ppt, hc)
    backstop_usd = (bs.get("optimo") or {}).get("backstop_usd")
    b_pts = backstop_usd / ppt if backstop_usd else None
    tp = tp_nominal_study(win, ppt, hc)["tp_nominal_atr"] or None   # R-T4 p99
    ladder = derive_ladder(win)                              # f2/f3 + alloc
    lado = side_management(win).get("recomendacion")         # R-T6 diagnóstico

    be = derive_breakeven(
        win, ppt, b_pts=b_pts, tp_by_side=tp, legs=ladder["legs"],
        suelo=suelo, cancel_after_s=cancel_after_s, touches=touches,
        has_intrabar=has_intrabar)

    return {
        "suelo_mae_p95_ganadoras": suelo,
        "backstop_usd": backstop_usd,
        "b_pts": round(b_pts, 2) if b_pts else None,
        "tp_por_lado_atr": tp,
        "ladder": ladder,
        "lado": lado,
        "breakeven": be,
    }


def derive_breakeven(win, ppt, *, b_pts, tp_by_side, legs, suelo,
                     cancel_after_s, touches, has_intrabar) -> dict:
    """BE derivado in-sample: candidatos = percentiles del MFE de ganadoras;
    se EVALÚA cada uno bajo la convención PESIMISTA y solo se recomienda si
    MEJORA el neto frente a SIN BE (R-T3). Sin intrabar → no disponible."""
    if not has_intrabar or touches is None:
        return {"disponible": False, "be_atr": None,
                "motivo": "no disponible sin HOLC/intrabar"}
    mfes = sorted(s.mfe_atr for s in win if s.native_pnl_usd > 0 and s.mfe_atr > 0)
    if not mfes:
        return {"disponible": True, "be_atr": None,
                "motivo": "sin ganadoras con MFE — BE no aplica"}
    cands = sorted({round(pctl(mfes, q), 2) for q in (0.20, 0.35, 0.50)})
    base_net = _eval_net(win, ppt, legs=legs, b_pts=b_pts,
                         tp_by_side=tp_by_side, be_atr=None,
                         cancel_after_s=cancel_after_s, touches=touches)
    filas = []
    mejor = None
    for be in cands:
        net = _eval_net(win, ppt, legs=legs, b_pts=b_pts,
                        tp_by_side=tp_by_side, be_atr=be,
                        cancel_after_s=cancel_after_s, touches=touches)
        filas.append({"be_atr": be, "net_usd": net,
                      "delta_vs_sin_be_usd": round(net - base_net, 2)})
        if mejor is None or net > mejor["net_usd"]:
            mejor = {"be_atr": be, "net_usd": net}
    recomendar = mejor is not None and mejor["net_usd"] > base_net
    return {
        "disponible": True,
        "be_atr": mejor["be_atr"] if recomendar else None,
        "mejora_usd": round(mejor["net_usd"] - base_net, 2) if mejor else None,
        "base_net_sin_be_usd": base_net,
        "candidatos": filas,
        "motivo": ("mejora el neto bajo la convención pesimista"
                   if recomendar else
                   "ningún BE mejora bajo la convención pesimista — no se recomienda"),
    }


def _eval_net(win, ppt, *, legs, b_pts, tp_by_side, be_atr,
              cancel_after_s, touches) -> float:
    pnls = []
    for st in win:
        fav, be_ret = (touches or {}).get(st.number, ({}, {}))
        usd, _ = luxy_outcome(st, fav, be_ret, legs=legs, b_pts=b_pts,
                              tp_by_side=tp_by_side, be_atr=be_atr, ppt=ppt,
                              cancel_after_s=cancel_after_s)
        pnls.append(usd)
    return round(sum(pnls), 2)


# ---------------------------------------------------------------------------
# Evaluación de una ventana bajo unas palancas → métricas (Tabla A)
# ---------------------------------------------------------------------------

def eval_levers(eval_sts: list[SimTrade], levers: dict, ppt: float, *,
                cancel_after_s: float | None, touches: dict | None) -> dict:
    """Métricas (metrics_usd) al aplicar `levers` sobre `eval_sts`. No-participado
    = 0.0 para que net/DD sean comparables en el periodo (como eval_config v1)."""
    legs = levers["ladder"]["legs"]
    b_pts = levers["b_pts"]
    tp = levers["tp_por_lado_atr"]
    be = (levers.get("breakeven") or {}).get("be_atr")
    lado_reco = (levers.get("lado") or {})
    solo = None
    if lado_reco.get("accion") == "cortar":
        solo = lado_reco.get("lado_bueno")           # R-T6 (diagnóstico aplicado)
    pnls = []
    n_part = 0
    for st in eval_sts:
        if solo and st.side != solo:
            pnls.append(0.0)
            continue
        fav, be_ret = (touches or {}).get(st.number, ({}, {}))
        usd, part = luxy_outcome(st, fav, be_ret, legs=legs, b_pts=b_pts,
                                 tp_by_side=tp, be_atr=be, ppt=ppt,
                                 cancel_after_s=cancel_after_s)
        pnls.append(usd)
        n_part += 1 if part else 0
    m = metrics_usd(pnls)
    m["participacion_pct"] = (round(100 * n_part / len(eval_sts), 1)
                              if eval_sts else None)
    return m


# ---------------------------------------------------------------------------
# Estudio completo — Tablas A/B (SPEC A.3)
# ---------------------------------------------------------------------------

def _lever_summary(levers: dict) -> dict:
    ld = levers["ladder"]
    alloc = ld["alloc"]
    tp = levers["tp_por_lado_atr"] or {}
    be = levers.get("breakeven") or {}
    lado = levers.get("lado") or {}
    return {
        "SL_suelo_atr": levers["suelo_mae_p95_ganadoras"],
        "backstop_usd": levers["backstop_usd"],
        "TP_long_atr": tp.get("long"),
        "TP_short_atr": tp.get("short"),
        "C1": alloc[0], "C2": alloc[1], "C3": alloc[2],
        "levels_atr": ld["levels"],
        "BE_atr": be.get("be_atr"),
        "lado": (lado.get("accion") + " " + (lado.get("lado_malo") or "")
                 if lado.get("accion") else "ambos"),
    }


def _convergencia(a: dict, b: dict) -> dict:
    """Indicador por palanca: coinciden/divergen entre in-sample-óptimo y
    OOS-óptimo (Tabla B). Numéricos: |Δ| relativo ≤ 15% = coinciden."""
    out = {}
    for k in ("SL_suelo_atr", "backstop_usd", "TP_long_atr", "TP_short_atr",
              "C1", "C2", "C3", "BE_atr", "lado"):
        va, vb = a.get(k), b.get(k)
        if va is None and vb is None:
            out[k] = "n/d"
        elif isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            base = max(abs(va), abs(vb), 1e-9)
            out[k] = "coinciden" if abs(va - vb) / base <= 0.15 else "divergen"
        else:
            out[k] = "coinciden" if va == vb else "divergen"
    return out


def _card(m: dict) -> dict:
    """Métricas → tesela del dashboard (contrato §1: net/pf/dd/worst/wr/part).
    dd en magnitud NEGATIVA (como mae_min del prototipo)."""
    return {"net": m.get("net_usd"), "pf": m.get("pf"),
            "dd": -(m.get("max_dd_usd") or 0.0), "worst": m.get("peor_trade_usd"),
            "wr": m.get("wr_pct"), "part": m.get("participacion_pct"),
            "n": m.get("n"),                      # LX-1 #4: la tabla reactiva usa N
            "n_perdedores": m.get("n_perdedores")}  # LX-7: PF honesto


# ── LX-3b — semáforo de robustez + $/trade + nota de muestra ────────────────
# El semáforo mide SOLO la fila OOS VALIDADA (palancas probadas en datos que no
# participaron en derivarlas): si la OOS se degrada, la estrategia se degrada.
ROBUSTEZ_PF_VERDE = 1.3       # 🟢 neto>0 y PF ≥ 1.3
ROBUSTEZ_PF_MIN = 1.0        # 🟡 neto>0 y PF en [1.0, 1.3) · 🔴 neto≤0 o PF < 1.0
RETENCION_N_MIN = 10        # OOS con menos trades → "muestra chica"
# LX-6 — tripwire de plausibilidad (fail-honest): un PF por encima de esto es
# absurdo para una estrategia real → delata intrabar desalineado (cola mal-TZ).
PF_ABSURDO = 50.0
PART_MIN_PLAUSIBLE = 90.0    # con C1 al mercado y sin corte de lado, debe ~100%
# LX-7 — el PF solo es EVALUABLE con al menos estos perdedores. Por debajo, un PF
# alto está disparado por aritmética (muestra sin apenas pérdidas), NO por
# corrupción → el tripwire NO debe declararlo "implausible". Mismo umbral que el
# front (MIN_PERDEDORES_PF en strategy_detail.html) — una sola regla, dos capas.
MIN_PERDEDORES_PF = 3


def tripwire_implausible(legs, lado_accion, participacion, pf, n_perdedores=None):
    """LX-6/LX-7 — (implausible, mensaje, aviso_muestra) del estudio.

    Con C1 al mercado (una pierna a profundidad ≤0) y SIN corte de lado, la
    participación DEBE ser ~100% (`leg_filled(0)=True` siempre); una participación
    baja delata joins intrabar desalineados (cola mal-TZ) → IMPLAUSIBLE.

    Un PF > PF_ABSURDO solo delata corrupción si es EVALUABLE (LX-7: la muestra
    tiene ≥ MIN_PERDEDORES_PF perdedores). Con menos perdedores el PF está
    disparado por aritmética, no corrupto: el tripwire NO declara "implausible" —
    devuelve `aviso_muestra` (estado propio "no evaluable por muestra"), coherente
    con la fila Crudo que ya rotula "n/s (N perdedor)". La participación anómala y
    el PF alto CON perdedores suficientes siguen disparando el tripwire."""
    c1_market = any(d <= 0 and w > 0 for d, w in (legs or ()))
    dir_both = lado_accion != "cortar"
    impl = []
    if c1_market and dir_both and participacion is not None \
            and participacion < PART_MIN_PLAUSIBLE:
        impl.append(f"participación {participacion}% con C1 al mercado "
                    f"(debería ~100%)")
    aviso_muestra = None
    if pf is not None and pf != float("inf") and pf > PF_ABSURDO:
        # LX-7: ¿es el PF evaluable? (None = caller sin dato → se juzga, retrocompat)
        if n_perdedores is not None and n_perdedores < MIN_PERDEDORES_PF:
            aviso_muestra = (
                f"muestra con muy pocos perdedores ({n_perdedores}) — el PF no es "
                f"evaluable; el estudio de riesgo tiene poco que decir aquí")
        else:
            impl.append(f"PF {round(pf, 1)} > {PF_ABSURDO}")
    msg = ("números implausibles: revisa alineación/cobertura intrabar — "
           + " · ".join(impl)) if impl else None
    return bool(impl), msg, aviso_muestra


def _expectativa(m: dict):
    """$/trade = neto ÷ n (expectativa por operación). None si no computable
    (guarda de división: sin n → no hay expectativa)."""
    net, n = m.get("net_usd"), m.get("n")
    return (net / n) if (net is not None and n) else None


def robustez_semaforo(oos: dict) -> dict:
    """Semáforo de robustez desde la fila OOS VALIDADA (net/pf):
    🟢 verde neto>0 y PF≥1.3 · 🟡 amarillo neto>0 y PF 1.0–1.3 · 🔴 rojo neto≤0
    o PF<1.0. Sobre métricas nulas/sin muestra → rojo (fail-honest)."""
    net, pf = oos.get("net_usd"), oos.get("pf")
    if net is None or pf is None or net <= 0 or pf < ROBUSTEZ_PF_MIN:
        verd = "rojo"
    elif pf >= ROBUSTEZ_PF_VERDE:
        verd = "verde"
    else:
        verd = "amarillo"
    return {"verdict": verd, "net_usd": net, "pf": pf, "n": oos.get("n")}


def retencion_oos(oos: dict, crudo_plus: dict) -> dict:
    """Retención = $/trade OOS ÷ $/trade Crudo+ (la métrica comparable entre
    muestras de distinto tamaño). Guarda de división → pct None. Marca muestra
    chica si n_oos < RETENCION_N_MIN."""
    e_oos, e_cp = _expectativa(oos), _expectativa(crudo_plus)
    pct = (round(100.0 * e_oos / e_cp, 1)
           if (e_oos is not None and e_cp not in (None, 0)) else None)
    n_oos = oos.get("n") or 0
    return {"expectativa_oos": e_oos, "expectativa_crudo_plus": e_cp,
            "pct": pct, "muestra_chica": n_oos < RETENCION_N_MIN, "n_oos": n_oos}


def muestra_banner(n_total: int, n_simulable: int, n_cola: int = 0,
                   n_inicio: int = 0, ultima_barra: str | None = None):
    """Nota de muestra (LX-5): enciende SIEMPRE que n_simulable < n_total, con el
    desglose por CAUSA y la acción. `n_simulable` = trades con ATR intrabar REAL
    (nunca cuenta ATR-estimados como simulables). El HOLC vive en NTEXECG, NO
    viaja en la lista. None cuando toda la muestra es simulable."""
    fuera = int(n_total) - int(n_simulable)
    if fuera <= 0:
        return None
    partes = []
    if n_cola:
        s = f"{n_cola} en la cola posterior a la última barra cosida"
        if ultima_barra:
            s += f" ({ultima_barra})"
        partes.append(s + " — reintegra cuando el updater alcance")
    if n_inicio:
        partes.append(f"{n_inicio} previos al inicio del almacén")
    resto = fuera - int(n_cola) - int(n_inicio)
    if resto > 0:
        partes.append(f"{resto} fuera de la cobertura intrabar")
    detalle = "; ".join(partes) if partes else str(fuera)
    return (f"{fuera} de {n_total} trades fuera de la cobertura HOLC almacenada "
            f"en NTEXECG ({detalle}) — Crudo+ los excluye de la simulación.")


def _entry_hour_et(tr, off) -> int:
    """Hora ET de la entrada — FUENTE ÚNICA para los toggles de sesión (R-T7) y
    para la nube del dashboard. Prioriza la que dejó el enriched
    (`tr.hour = (entry_ts + offset).hour`, línea de `enrich_with_bars`); si un
    trade no quedó enriquecido (`hour` None), la recompone con el MISMO offset ET
    del enriched — NUNCA con la hora cruda del CSV (que no lleva offset)."""
    from datetime import timedelta
    h = getattr(tr, "hour", None)
    if h is not None:
        return h
    return (tr.entry_ts + timedelta(minutes=int(off or 0))).hour


def _dashboard_payload(sts: list[SimTrade], by_number: dict, levers_in: dict,
                       ppt: float, crudo: dict, config_m: dict,
                       off: int = 0) -> dict:
    """Contrato §1 de la receta, derivado del estudio (presentación, sin
    recomputar nada pesado): nube de trades, base/config, reco con zonas/días de
    la partición ÚNICA (R-T7), time-stop, unidades. Determinista."""
    from statistics import median

    atr_med = median(s.atr_pts for s in sts) if sts else None
    rows = []
    for s in sts:
        tr = by_number.get(s.number)
        hr = _entry_hour_et(tr, off) if tr is not None else None
        dow = tr.entry_ts.weekday() if tr is not None else None
        dur = ((tr.exit_ts - tr.entry_ts).total_seconds() / 60.0
               if tr is not None and tr.exit_ts else None)
        rows.append({"pnl": s.native_pnl_usd, "hr": hr, "dow": dow,
                     "long": s.side == "long", "dur": dur,
                     "in": bool(s.in_sample),
                     "mfe": round(s.mfe_pts * ppt, 1),
                     "mae": round(-s.mae_pts * ppt, 1)})
    nube = [{"i": k + 1, "mfe": r["mfe"], "mae": r["mae"],
             "pnl": round(r["pnl"], 1), "long": r["long"],
             "hr": r["hr"], "dow": r["dow"], "in": r["in"]}
            for k, r in enumerate(rows)]

    def _agg(sel):
        p = [r["pnl"] for r in sel]
        m = metrics_usd(p) if p else {"n": 0}
        return {"n": len(sel), "net": round(sum(p)) if p else 0,
                "wr": m.get("wr_pct"), "pf": m.get("pf")}

    zones = []
    for name, et, hours in LUXY_ZONES:
        sel = [r for r in rows if r["hr"] in hours]
        a = _agg(sel)
        a.update({"name": name, "hours": hours, "et": et,
                  "losing": bool(a["net"] < 0 and a["n"] >= 8)})
        zones.append(a)
    days = []
    for d in range(7):
        sel = [r for r in rows if r["dow"] == d]
        if not sel:
            continue
        a = _agg(sel)
        a.update({"dow": d, "name": _DAY_ES[d],
                  "losing": bool(a["net"] < 0 and a["n"] >= 8)})
        days.append(a)

    buckets = [("0–30", 0, 30), ("30–60", 30, 60), ("60–120", 60, 120),
               ("120–240", 120, 240), ("240+", 240, 1e12)]
    tsb = [{"range": lb, "n": sum(1 for r in rows
                                  if r["dur"] is not None and lo <= r["dur"] < hi),
            "net": round(sum(r["pnl"] for r in rows
                             if r["dur"] is not None and lo <= r["dur"] < hi))}
           for lb, lo, hi in buckets]
    timestop = {"verdict": "descartado", "buckets": tsb,
                "why": "cortar las operaciones largas quita también las "
                       "recuperaciones (sesgo de supervivencia) — diagnóstico, "
                       "no es palanca aplicable."}

    ld = levers_in.get("ladder") or {}
    alloc = ld.get("alloc") or [10, 0, 0]
    levels = ld.get("levels") or [0.0]
    tp = levers_in.get("tp_por_lado_atr") or {}
    be = (levers_in.get("breakeven") or {}).get("be_atr")
    lado = levers_in.get("lado") or {}
    dir_reco = (lado.get("lado_bueno") if lado.get("accion") == "cortar"
                else "both")
    tp_long = tp.get("long")

    def _usd(x_atr):
        return round(x_atr * atr_med * ppt) if (x_atr and atr_med) else None

    def _pts(x_atr):
        return round(x_atr * atr_med, 1) if (x_atr and atr_med) else None

    reco = {
        "sl_usd": levers_in.get("backstop_usd"),
        "sl_pts": levers_in.get("b_pts"),
        "tp_usd": _usd(tp_long), "tp_pts": _pts(tp_long),
        "tp_long_atr": tp_long, "tp_short_atr": tp.get("short"),
        "be_usd": _usd(be), "be_atr": be,
        "l2_usd": _usd(levels[1]) if len(levels) > 1 else None,
        "l3_usd": _usd(levels[2]) if len(levels) > 2 else None,
        "l2_pts": _pts(levels[1]) if len(levels) > 1 else None,
        "l3_pts": _pts(levels[2]) if len(levels) > 2 else None,
        "l2_atr": levels[1] if len(levels) > 1 else None,
        "l3_atr": levels[2] if len(levels) > 2 else None,
        "alloc": alloc, "fill2": round((ld.get("f2") or 0) * 100),
        "fill3": round((ld.get("f3") or 0) * 100),
        "why_alloc": ld.get("why_alloc"), "dir": dir_reco,
        "zones": zones, "days": days,
    }

    recon_ok = len(sts)
    base_net = crudo.get("net_usd") or 0.0
    cfg_net = config_m.get("net_usd") or 0.0
    flip = base_net != 0 and (base_net > 0) != (cfg_net > 0)
    big = base_net > 0 and cfg_net > 3 * base_net
    fragile = bool(flip or big)
    notes = []
    if flip:
        notes.append("flip de signo crudo→config")
    if big:
        notes.append("mejora >3× (revisar sobreajuste)")
    notes.append("régimen omitido (no disponible barato en este estudio)")

    entries = sorted(getattr(by_number.get(s.number), "entry_price", None)
                     for s in sts
                     if getattr(by_number.get(s.number), "entry_price", None))
    ref_price = entries[len(entries) // 2] if entries else None
    return {
        "pv": ppt, "n": len(sts), "recon_ok": recon_ok, "fragile": fragile,
        "notes": notes, "ref_price": ref_price,
        "mfe_max": max((t["mfe"] for t in nube), default=0),
        "mae_min": min((t["mae"] for t in nube), default=0),
        "trades": nube, "base": _card(crudo), "config": _card(config_m),
        "reco": reco, "timestop": timestop,
        "units": {"pv": ppt, "show_pts": ppt <= 1000,
                  "atr_med_pts": round(atr_med, 2) if atr_med else None},
        "zones_partition": [{"name": n, "et": e, "hours": h}
                            for n, e, h in LUXY_ZONES],
    }


# LX-12 — banners de degradado por MOTIVO (el front pinta rojo el de contención).
_AVISO_SIN_HOLC = ("master DEGRADADO (sin HOLC): fills con corte y BREAKEVEN "
                   "no disponibles; solo crudo + palancas sin intrabar.")
_AVISO_NO_CONFIABLE = ("master y HOLC no comparten contorno de contrato "
                       "(¿roll/back-adjust?) — corrige el Merge policy en "
                       "NinjaTrader y reintegra. Estudio DEGRADADO: solo crudo, "
                       "sin palancas intrabar (fail-honest LX-12).")


def _avisos_degradado(motivo: str | None) -> list[str]:
    return [_AVISO_NO_CONFIABLE if motivo == "intrabar_no_confiable"
            else _AVISO_SIN_HOLC]


def luxy_study(trades, ppt: float, *, oos: float = 0.3,
               cancel_after_s: float | None = CANCEL_AFTER_MAX_S,
               keys5=None, idx5=None, bars5=None,
               has_intrabar: bool = True,
               fecha: str | None = None, off: int = 0,
               degradado_motivo: str | None = None) -> dict:
    """Estudio Luxy completo (Tablas A/B) sobre los `trades` enriquecidos del
    master. `keys5/idx5/bars5` = HOLC del motor (para el intrabar del BE, R-T2);
    ausentes o has_intrabar=False → estudio DEGRADADO/limitado honesto.

    Determinista: mismas entradas → mismo dict (funciones puras, orden fijo)."""
    from scripts.lab_analyze import split_in_out

    if cancel_after_s is not None:
        cancel_after_s = min(float(cancel_after_s), CANCEL_AFTER_MAX_S)
    split_in_out(trades, oos)                              # split por tiempo compartido
    sts = from_trades(trades, ppt)
    by_number = {t.number: t for t in trades}
    sts_in = [s for s in sts if s.in_sample]
    sts_oos = [s for s in sts if not s.in_sample]

    intrabar = has_intrabar and bool(keys5)

    # Fecha de corte del split (viejo=derivar, reciente=probar) — persistida.
    corte_idx = len(sts_in)
    cutoff = None
    if sts and corte_idx < len(trades):
        try:
            cutoff = trades[corte_idx].entry_ts.isoformat()
        except Exception:
            cutoff = None

    # Toques del motor a los niveles que el BE necesita (favor: BE cands + TP;
    # adverso: prueba de retorno). Se calculan una vez por trade (R-T2).
    def _build_touches(win_sts, tp_by_side, be_cands):
        if not intrabar:
            return None
        tp_levels = {v for v in (tp_by_side or {}).values() if v}
        tt = {}
        for st in win_sts:
            tr = by_number.get(st.number)
            tt[st.number] = _touches_for(tr, tp_levels, be_cands,
                                         keys5, idx5, bars5)
        return tt

    # BE candidates dependen de la ventana → dos pasadas independientes.
    def _levers(win):
        tp = tp_nominal_study(win, ppt)["tp_nominal_atr"] or None
        mfes = sorted(s.mfe_atr for s in win
                      if s.native_pnl_usd > 0 and s.mfe_atr > 0)
        be_cands = ([round(pctl(mfes, q), 2) for q in (0.20, 0.35, 0.50)]
                    if mfes else [])
        tt = _build_touches(win, tp, be_cands)
        return derive_levers(win, ppt, cancel_after_s=cancel_after_s,
                             touches=tt, has_intrabar=intrabar), tt

    levers_in, touches_in = _levers(sts_in) if sts_in else ({}, None)
    levers_oos, touches_oos = _levers(sts_oos) if sts_oos else ({}, None)

    # Toques de TODA la muestra con las palancas IN-SAMPLE (para la fila
    # In-sample de la Tabla A = con palancas, toda la muestra).
    touches_all = None
    if intrabar and levers_in:
        tp_in = levers_in.get("tp_por_lado_atr")
        be_in = (levers_in.get("breakeven") or {}).get("be_atr")
        cands = [be_in] if be_in else []
        tp_levels = {v for v in (tp_in or {}).values() if v}
        touches_all = {}
        for st in sts:
            tr = by_number.get(st.number)
            touches_all[st.number] = _touches_for(
                tr, tp_levels, cands, keys5, idx5, bars5)

    # ── Tabla A ──
    # CRUDO = señal SIN palancas, TODA la muestra (incluye trades sin ATR que
    # el universo de sims excluye) — el crudo honesto del listado. Funciona
    # también en degradado (no necesita intrabar).
    crudo = metrics_usd([float(getattr(t, "pnl_usd", 0.0) or 0.0)
                         for t in trades])
    crudo["participacion_pct"] = 100.0
    fila_in = (eval_levers(sts, levers_in, ppt, cancel_after_s=cancel_after_s,
                           touches=touches_all) if levers_in else {"n": 0})
    fila_oos = (eval_levers(sts_oos, levers_in, ppt,
                            cancel_after_s=cancel_after_s,
                            touches=touches_all) if levers_in and sts_oos
                else {"n": 0})

    def _rowA(nombre, m):
        return {"fila": nombre, "net_usd": m.get("net_usd"),
                "pf": m.get("pf"), "max_dd_usd": m.get("max_dd_usd"),
                "peor_trade_usd": m.get("peor_trade_usd"),
                "participacion_pct": m.get("participacion_pct"),
                "wr_pct": m.get("wr_pct"), "n": m.get("n"),
                "n_perdedores": m.get("n_perdedores")}   # LX-7

    tabla_a = [_rowA("Crudo", crudo), _rowA("In-sample", fila_in),
               _rowA("OOS", fila_oos)]

    # ── Tabla B (derivación INDEPENDIENTE por ventana + convergencia) ──
    sum_in = _lever_summary(levers_in) if levers_in else {}
    sum_oos = _lever_summary(levers_oos) if levers_oos else {}
    tabla_b = {
        "in_sample_optimo": sum_in,
        "oos_optimo": sum_oos,
        "convergencia": (_convergencia(sum_in, sum_oos)
                         if sum_in and sum_oos else {}),
        "nota_oos": ("La fila OOS-óptimo es un ESPEJO DE ROBUSTEZ — NO es la "
                     "config a usar. Lo aplicable sale SIEMPRE de la fila "
                     "in-sample probada en OOS (R-T10)."),
    }

    # ── Payload de presentación para el dashboard L3 (contrato §1) ──
    dashboard = None
    if intrabar and levers_in and sts:
        dashboard = _dashboard_payload(sts, by_number, levers_in, ppt,
                                       crudo, fila_in, off=off)
        # L7a — Ventana de operación + rango/duración por lado NATIVAS en Luxy:
        # REUSO del helper de v1 (RIES-W `_listado_crudo`/`_ventana_operacion`)
        # sobre TODO el listado crudo + el offset ET del enriched → paridad
        # numérica exacta con v1, cero duplicación. v1 muere en L7b.
        from scripts.nt_riesgo import _listado_crudo
        _lc = _listado_crudo(trades, off)
        dashboard["ventana_operacion"] = _lc["ventana_operacion"]
        dashboard["duracion_h_por_lado"] = _lc["duracion_h_por_lado"]
        # LX-3 — filas VALIDADAS de la tabla reactiva:
        #  · Crudo    = lista base, SIN palancas, n = TODOS los trades (121 ES).
        #  · Crudo+   = TODAS las palancas sobre el 100% de la muestra SIMULABLE
        #               (todos los `sts`, viejos+recientes) — la semántica de la
        #               vieja fila In-sample de la Tabla A (`fila_in`).
        #  · OOS      = espejo: palancas del in-sample SOLO sobre la muestra
        #               apartada (R-T10, `fila_oos`).
        # `n_total`/`n_simulable` alimentan la nota honesta de muestra (los trades
        # sin intrabar quedan fuera de los sims pero dentro del crudo).
        dashboard["table3"] = {
            "crudo": _rowA("Crudo", crudo),
            "crudo_plus": _rowA("Crudo+", fila_in),
            "oos": _rowA("OOS", fila_oos),
        }
        dashboard["cutoff_i"] = len(sts_in)
        # LX-5 — DEFINICIÓN ÚNICA de simulable para Luxy = trade con ATR intrabar
        # REAL (los que entraron a `sts`). Los NO simulables se cuentan aparte y
        # se clasifican desde los propios datos del estudio (no del manifest v1):
        # cola posterior a la última barra (cosida) vs previos al inicio del
        # almacén. Así n_simulable == Crudo+ n == recon == sts_in+sts_oos.
        from datetime import timedelta as _td
        _sim = {s.number for s in sts}
        _first = min(bars5) if bars5 else None
        _last = max(bars5) if bars5 else None
        _delta = _td(minutes=off)
        n_cola = n_inicio = 0
        for t in trades:
            if t.number in _sim:
                continue
            _et = t.entry_ts + _delta
            if _last is not None and _et > _last:
                n_cola += 1                          # cola posterior (v1 estimaría)
            else:
                n_inicio += 1                        # previos al inicio / hueco
        dashboard["n_total"] = len(trades)
        dashboard["n_simulable"] = len(sts)
        dashboard["n_no_simulable"] = len(trades) - len(sts)
        dashboard["n_estimados"] = n_cola            # ATR-estimados en v1, fuera aquí
        dashboard["n_inicio"] = n_inicio
        dashboard["ultima_barra"] = _last.isoformat() if _last else None
        # LX-6 — tripwire de plausibilidad (barato): con C1 al mercado (una pierna
        # a profundidad ≤0) y SIN corte de lado, la participación DEBE ser ~100%
        # (leg_filled(0)=True siempre); una PF absurda o participación baja delatan
        # joins intrabar desalineados (cola mal-TZ). El semáforo NO se enciende.
        _impl, _msg, _aviso_muestra = tripwire_implausible(
            (levers_in.get("ladder") or {}).get("legs") or (),
            (levers_in.get("lado") or {}).get("accion"),
            fila_in.get("participacion_pct"), fila_in.get("pf"),
            fila_in.get("n_perdedores"))                 # LX-7: PF evaluable o no
        dashboard["implausible"] = _impl
        dashboard["implausible_msg"] = _msg
        # LX-7 — estado PROPIO "no evaluable por muestra" (≠ implausible): PF alto
        # con muy pocos perdedores no es corrupción, es muestra sin significado.
        dashboard["pf_no_evaluable"] = bool(_aviso_muestra)
        dashboard["pf_no_evaluable_msg"] = _aviso_muestra
        # LX-3b — semáforo de robustez (OOS validada), retención $/trade y banner
        # de muestra (todo del payload; la estimación NO enciende semáforo).
        dashboard["robustez"] = robustez_semaforo(fila_oos)
        dashboard["retencion"] = retencion_oos(fila_oos, fila_in)
        dashboard["muestra_banner"] = muestra_banner(
            len(trades), len(sts), n_cola, n_inicio,
            _last.isoformat() if _last else None)

    return {
        "version": 3,               # v3: BE same_bar recortado (walk aditivo)
        "be_walk": "be_return_minutes/2",
        "dashboard": dashboard,     # L3: nube + reco + zonas/días/time-stop
        "fecha": fecha,
        "degradado": not intrabar,
        # LX-12 — por qué degrada: "intrabar_no_confiable" (roll/back-adjust) o
        # None/sin_holc. El front pinta el banner rojo específico de contención.
        "degradado_motivo": (degradado_motivo if not intrabar else None),
        "usd_por_punto": ppt,                       # R-T9 (del master)
        "cancel_after_s": cancel_after_s,
        "oos_frac": oos,
        # LX-5 — doble universo sin ambigüedad: trades (todos) vs simulables (sts).
        "split": {"n_total": len(sts), "n_in_sample": len(sts_in),
                  "n_oos": len(sts_oos), "cutoff_ts": cutoff,
                  "n_trades_in": sum(1 for t in trades if t.in_sample),
                  "n_trades_oos": sum(1 for t in trades if not t.in_sample),
                  "nota": "split por tiempo: viejo=derivar, reciente=probar "
                          "(compartido con el motor)"},
        "tabla_a": tabla_a,
        "tabla_b": tabla_b,
        "levers_in_sample": levers_in,
        "levers_oos": levers_oos,
        "avisos": ([] if intrabar else _avisos_degradado(degradado_motivo)),
    }


# ---------------------------------------------------------------------------
# Reconciliación contra v1 (C): sin BE y con las MISMAS palancas, luxy_outcome
# debe reproducir mr_sims.ladder_outcome trade a trade.
# ---------------------------------------------------------------------------

def activacion_from_study(study: dict) -> dict:
    """Config APLICABLE del estudio Luxy → las MISMAS llaves del Puente
    (`routes_riesgo._activacion_json`), para reusar diff/merge/deriva sin
    duplicar. **R-T10: SOLO la fila IN-SAMPLE** (`levers_in_sample`) — la fila
    OOS es espejo de robustez y JAMÁS se aplica.

    Mapea: backstop_points (b_pts), tp_nominal_long/short (×ATR p99),
    scale_entry (quantities=alloc derivado, levels=profundidades ×ATR — el
    `mode` lo PRESERVA el merge, NX-11), entry_reserve_timeout_seconds si el
    estudio trae cancel_after. El BREAKEVEN NO se mapea (no hay palanca de BE en
    el despacho — L5 lo trata como informativo)."""
    lev = (study or {}).get("levers_in_sample") or {}
    out: dict = {}
    b_pts = lev.get("b_pts")
    if lev.get("backstop_usd") and b_pts:
        out["backstop_points"] = round(float(b_pts), 2)
    tp = lev.get("tp_por_lado_atr") or {}
    if tp.get("long"):
        out["tp_nominal_long"] = tp["long"]
    if tp.get("short"):
        out["tp_nominal_short"] = tp["short"]
    ca = study.get("cancel_after_s")
    if ca:
        out["entry_reserve_timeout_seconds"] = int(ca)
    ld = lev.get("ladder") or {}
    alloc = ld.get("alloc") or []
    levels = ld.get("levels") or [0.0]
    if alloc and any(a > 0 for a in alloc[1:]):        # hay escalera (C2/C3)
        out["scale_entry"] = {
            "mode": "execute",                          # el merge preserva el vivo
            "quantities": list(alloc),
            "levels": [round(float(x), 2) for x in levels[1:]],
            "max_micro_contracts": sum(alloc) or 10,
        }
    return out


def breakeven_informativo(study: dict) -> dict | None:
    """Si el estudio recomienda BE (in-sample), devuelve la info para MOSTRARLA
    como 'palanca no aplicable aún — informativa'. NO se escribe en producción
    (no hay palanca de BE en el despacho)."""
    be = ((study or {}).get("levers_in_sample") or {}).get("breakeven") or {}
    if be.get("be_atr"):
        return {"be_atr": be["be_atr"], "mejora_usd": be.get("mejora_usd")}
    return None


def reconcile_trade_vs_v1(st: SimTrade, legs: tuple, b_pts: float | None,
                          tp_by_side: dict | None, ppt: float,
                          cancel_after_s: float | None) -> tuple[float, float]:
    """(luxy_sin_be, v1_ladder) para el MISMO trade/palancas — deben coincidir
    (sin BE, luxy_outcome ≡ ladder_outcome del motor)."""
    hc = HaircutCfg()
    lux, _ = luxy_outcome(st, {}, {}, legs=legs, b_pts=b_pts,
                          tp_by_side=tp_by_side, be_atr=None, ppt=ppt,
                          cancel_after_s=cancel_after_s)
    v1, fw, _ = ladder_outcome(st, legs, b_pts, tp_by_side, ppt, hc,
                               cancel_after_s)
    return round(lux, 6), round(v1 if fw > 0 else 0.0, 6)


# ---------------------------------------------------------------------------
# Carga del master (compartida por el estudio y la evaluación de palancas
# custom) + evaluación de palancas movidas (Recalcular del dashboard) vía el
# MISMO evaluador de L2 (`eval_levers`) — mismos números que llamarlo directo.
# ---------------------------------------------------------------------------

def _load_master(base_dir):
    """(manifest, trades enriquecidos, ppt, keys5, idx5, bars5, has_intrabar,
    off) del master integrado. `off` = offset ET del enriched (RIES-W: la
    ventana de operación lo necesita para calcar `hora_et`; paridad con v1).
    Degradado / sin HOLC → has_intrabar False y off 0."""
    import json
    from scripts.lab_analyze import (
        detect_tz_offset, enrich_with_bars, load_holc, load_holc_from_path,
        parse_luxalgo_csv,
    )
    man = json.loads((base_dir / "manifest.json").read_text(encoding="utf-8"))
    ppt = float(man["usd_por_punto"]["usado"])          # R-T9: del master
    activo = man["activo"]
    # LX-12 — intrabar NO confiable (HOLC no contiene los precios: roll/back-
    # adjust) degrada IGUAL que la ausencia de HOLC: solo-crudo, sin palancas
    # intrabar (fail-honest — jamás derivar de un intrabar que no los contiene).
    degradado = bool(man.get("degradado")
                     or (man.get("holc") or {}).get("degradado")
                     or man.get("intrabar_no_confiable"))
    trades = parse_luxalgo_csv(base_dir / "master.csv")
    keys5 = idx5 = bars5 = None
    has_intrabar = False
    off = 0
    if not degradado:
        try:
            # LX-4 — prioriza el snapshot HOLC por-clave (cosido al integrar):
            # así el estudio hereda la cobertura de la cola por R-T2. Fallback al
            # HOLC global para masters viejos sin snapshot (→ reintegrar cose).
            _snap = base_dir / "holc_5m.csv"
            bars5 = (load_holc_from_path(_snap) if _snap.exists()
                     else load_holc(activo, "5m"))
            off, _s, _d = detect_tz_offset(trades, bars5)
            enrich_with_bars(trades, bars5, off)
            keys5 = sorted(bars5)
            idx5 = {k: i for i, k in enumerate(keys5)}
            has_intrabar = True
        except Exception:
            has_intrabar = False
            off = 0
    return man, trades, ppt, keys5, idx5, bars5, has_intrabar, off


def _overrides_to_levers(base: dict, o: dict, atr_med, ppt) -> dict:
    """Palancas movidas por el operador (en USD) → dict de palancas en ×ATR,
    sobre las derivadas del estudio. USD → ×ATR con el ATR mediano y el $/punto
    del master (nunca del CSV)."""
    o = o or {}
    lev = {
        "suelo_mae_p95_ganadoras": base.get("suelo_mae_p95_ganadoras"),
        "backstop_usd": base.get("backstop_usd"),
        "b_pts": base.get("b_pts"),
        "tp_por_lado_atr": dict(base.get("tp_por_lado_atr") or {}),
        "ladder": dict(base.get("ladder") or {}),
        "lado": base.get("lado"),
        "breakeven": dict(base.get("breakeven") or {}),
    }
    if o.get("sl_usd") is not None:
        lev["backstop_usd"] = float(o["sl_usd"])
        lev["b_pts"] = float(o["sl_usd"]) / ppt
    if o.get("tp_usd") is not None and atr_med:
        a = float(o["tp_usd"]) / ppt / atr_med
        lev["tp_por_lado_atr"] = {"long": a, "short": a}
    if o.get("be_off"):
        lev["breakeven"] = {"disponible": True, "be_atr": None}
    elif o.get("be_usd") is not None and atr_med:
        lev["breakeven"] = {"disponible": True,
                            "be_atr": float(o["be_usd"]) / ppt / atr_med}
    ld = lev["ladder"]
    if (o.get("l2_usd") is not None or o.get("l3_usd") is not None) \
            and atr_med and ld.get("alloc"):
        levels = list(ld.get("levels") or [0.0, 0.0, 0.0])
        if o.get("l2_usd") is not None:
            levels[1] = float(o["l2_usd"]) / ppt / atr_med
        if o.get("l3_usd") is not None:
            levels[2] = float(o["l3_usd"]) / ppt / atr_med
        ld["levels"] = levels
        ld["legs"] = tuple((d, c / TOTAL_MICROS)
                           for d, c in zip(levels, ld["alloc"]) if c > 0)
    d = o.get("dir")
    if d in ("long", "short"):
        lev["lado"] = {"accion": "cortar", "lado_bueno": d}
    elif d == "both":
        lev["lado"] = None
    return lev


def evaluate_overrides(clave: str, motor_dir, overrides: dict, *,
                       oos: float = 0.3,
                       cancel_after_s: float | None = CANCEL_AFTER_MAX_S) -> dict:
    """RECALCULAR del dashboard: evalúa las palancas movidas con el evaluador de
    L2 (`eval_levers` → `luxy_outcome`) sobre el master. Devuelve las teselas
    VALIDADAS (base/config/oos). Mismos números que llamar al evaluador directo
    con esas palancas."""
    from statistics import median
    from pathlib import Path

    from scripts.lab_analyze import split_in_out

    if cancel_after_s is not None:
        cancel_after_s = min(float(cancel_after_s), CANCEL_AFTER_MAX_S)
    _man, trades, ppt, keys5, idx5, bars5, intrabar, off = _load_master(
        Path(motor_dir) / clave)
    split_in_out(trades, oos)
    sts = from_trades(trades, ppt)
    if not sts:
        return {"error": "sin universo ATR (master degradado) — no se puede "
                         "evaluar palancas intrabar"}
    by_number = {t.number: t for t in trades}
    sts_in = [s for s in sts if s.in_sample]
    sts_oos = [s for s in sts if not s.in_sample]
    atr_med = median(s.atr_pts for s in sts)

    base_levers = derive_levers(
        sts_in, ppt, cancel_after_s=cancel_after_s, touches=None,
        has_intrabar=False)              # base sin BE (BE viene del override)
    lev = _overrides_to_levers(base_levers, overrides, atr_med, ppt)

    touches = None
    if intrabar:
        tp_levels = {v for v in (lev["tp_por_lado_atr"] or {}).values() if v}
        be = (lev["breakeven"] or {}).get("be_atr")
        cands = [be] if be else []
        touches = {s.number: _touches_for(by_number.get(s.number), tp_levels,
                                          cands, keys5, idx5, bars5)
                   for s in sts}

    # LX-2 — toggles por sesión/día: excluir trades por ZONA canónica (R-T7,
    # mismo `zone_of_hour` de sesiones_et) o por día (dow 0-6) ANTES de evaluar
    # cada ventana. `zones_off` = nombres de zona; `days_off` = dow (0=lunes).
    # No persiste ni entra en Aplicar (diagnóstico dentro de muestra).
    zones_off = set(overrides.get("zones_off") or [])
    days_off = set(int(d) for d in (overrides.get("days_off") or []))

    def _passes(s) -> bool:
        tr = by_number.get(s.number)
        if tr is None:
            return True
        hr = _entry_hour_et(tr, off)          # hora ET (fuente única, con offset)
        if zones_off and zone_of_hour(hr) in zones_off:
            return False
        if days_off and tr.entry_ts.weekday() in days_off:
            return False
        return True

    _filt = bool(zones_off or days_off)
    sts_f = [s for s in sts if _passes(s)] if _filt else sts
    sts_oos_f = [s for s in sts_oos if _passes(s)] if _filt else sts_oos

    # LX-3 — `config` es CRUDO+ : las palancas movidas sobre el 100% de la
    # muestra SIMULABLE (TODOS los sts, viejos+recientes juntos). La fila OOS es
    # el ESPEJO con las MISMAS palancas SOLO sobre el subconjunto apartado
    # (R-T10). Los toggles LX-2 (zonas/días) aplican a AMBAS por igual.
    # `touches` está cacheado por número → sirve a ambos conjuntos.
    crudo_plus = eval_levers(sts_f, lev, ppt, cancel_after_s=cancel_after_s,
                             touches=touches) if sts_f else {"n": 0}
    oosm = eval_levers(sts_oos_f, lev, ppt, cancel_after_s=cancel_after_s,
                       touches=touches) if sts_oos_f else {"n": 0}
    crudo = metrics_usd([t.pnl_usd for t in trades])
    return {
        "validado": True, "clave": clave,
        "base": _card(crudo), "config": _card(crudo_plus), "oos": _card(oosm),
        # LX-3b — Recalcular refresca el semáforo (OOS validada) y la retención.
        "robustez": robustez_semaforo(oosm),
        "retencion": retencion_oos(oosm, crudo_plus),
        "levers": _lever_summary(lev),
    }


# ---------------------------------------------------------------------------
# Runner CLI — carga el master de MotorRiesgo/<clave>, enriquece con el HOLC
# (reuso del núcleo del Lab; nada de reconstrucción propia) y persiste el
# estudio en runs/luxy_<fecha>.json. Lo lanza la sub-pestaña Luxy como JOB
# (patrón Calcular). El motor de v1 NO se toca.
# ---------------------------------------------------------------------------

def run_for_clave(clave: str, motor_dir, *, oos: float = 0.3,
                  cancel_after_s: float | None = CANCEL_AFTER_MAX_S,
                  fecha: str | None = None) -> dict:
    """Corre el estudio Luxy sobre el master integrado de `clave` y escribe
    runs/luxy_<fecha>.json. Determinista (mismo master → mismo JSON). Master
    degradado (holc nulo) o sin HOLC en disco → estudio LIMITADO honesto."""
    import json
    from datetime import date
    from pathlib import Path

    base_dir = Path(motor_dir) / clave
    man, trades, ppt, keys5, idx5, bars5, has_intrabar, off = _load_master(
        base_dir)
    fecha = fecha or date.today().isoformat()

    # LX-12 — si el master quedó marcado intrabar_no_confiable, el estudio
    # degrada con el banner ROJO específico (no el genérico "sin HOLC").
    motivo = ("intrabar_no_confiable" if man.get("intrabar_no_confiable")
              else None)
    study = luxy_study(trades, ppt, oos=oos, cancel_after_s=cancel_after_s,
                       keys5=keys5, idx5=idx5, bars5=bars5,
                       has_intrabar=has_intrabar, fecha=fecha, off=off,
                       degradado_motivo=motivo)
    study["clave"] = clave
    study["contencion"] = man.get("contencion")     # LX-12 (ficha/banner del front)
    study["master"] = {"integrado": man.get("integrado"),
                       "sha256": (man.get("export") or {}).get("sha256_master"),
                       "n_trades": (man.get("trades") or {}).get("n")}
    # LX-9 — identidad estable del estudio (fecha + sha del master): el navegador
    # la guarda junto a la exploración; si cambia (reintegrar/recalcular otro
    # día), la exploración vieja se descarta. Sin efecto en el server.
    if study.get("dashboard"):
        _sha = (man.get("export") or {}).get("sha256_master") or ""
        study["dashboard"]["estudio_id"] = f"{fecha}:{_sha[:12]}"
    runs = base_dir / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    out = runs / f"luxy_{fecha}.json"
    out.write_text(json.dumps(study, indent=1, ensure_ascii=False,
                              sort_keys=True), encoding="utf-8")
    return study


def main() -> None:
    import argparse
    import os
    from pathlib import Path

    ap = argparse.ArgumentParser(prog="mr_luxy",
                                 description="Estudio Luxy (Riesgo v2) — L2")
    ap.add_argument("clave", help="carpeta MotorRiesgo/<ACTIVO>_<codigo>")
    ap.add_argument("--oos", type=float, default=0.3)
    ap.add_argument("--fecha", default=None)
    ap.add_argument("--evaluar", default=None,
                    help="JSON de palancas movidas (USD) → evalúa con el "
                         "evaluador de L2 y escribe el resultado en stdout "
                         "(RECALCULAR del dashboard, sin persistir el estudio)")
    args = ap.parse_args()
    import json
    motor_dir = Path(os.environ.get("MOTOR_RIESGO_DIR") or "MotorRiesgo")

    if args.evaluar is not None:
        overrides = json.loads(args.evaluar) if args.evaluar.strip() else {}
        res = evaluate_overrides(args.clave, motor_dir, overrides,
                                 oos=args.oos)
        print("LUXY_EVAL_JSON " + json.dumps(res, ensure_ascii=False,
                                             sort_keys=True))
        return

    study = run_for_clave(args.clave, motor_dir, oos=args.oos, fecha=args.fecha)
    crudo = next(f for f in study["tabla_a"] if f["fila"] == "Crudo")
    print(f"✅ Luxy {args.clave} · degradado={study['degradado']} · "
          f"crudo net ${crudo['net_usd']:,.2f} (n={crudo['n']}) → "
          f"runs/luxy_{study['fecha']}.json")


if __name__ == "__main__":
    main()
