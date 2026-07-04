"""Laboratorio — núcleo PURO de agregación (una sola fuente de verdad).

Opera sobre la MATRIZ DE FEATURES por trade (las filas que el camino A cachea
en `REPORTES/lab_features_<SYM>.json`): dicts con
  {entry_ts, side, pnl_pct, pnl_usd, mae_pct, mfe_pct, atr_entry, atr_pct,
   mae_atr, mfe_atr, hour, in_sample, sub_volume, sub_atr, sub_vwap, sub_time,
   regime_1h, regime_4h, ema_with}

Lo llaman AMBOS consumidores — el reporte offline (`scripts/lab_analyze.py`)
y el endpoint del visor (`app/web/routes_lab.py`) — para que el número en
pantalla sea IDÉNTICO al del reporte (candado del camino B). Sin I/O, sin DB.

Selección (el "what-if" del UI y las tablas del reporte):
  {"subs":   {"volume_relative": 60, ...},   # subscore·100 ≥ umbral (AND)
   "regime": {"tf": "1h", "allowed": ["trending_bull", ...]},  # unknown PASA
   "ema":    ["1h20", ...]}                   # con-tendencia en esas claves
"""
from __future__ import annotations

# Guarda anti-espejismo del visor/reporte: out-of-sample con n < 15 no es
# confiable (criterio acordado en la revisión del camino A).
LOW_N_OUT = 15

_SUB_ATTR = {
    "volume_relative": "sub_volume",
    "atr_normalized": "sub_atr",
    "vwap_position": "sub_vwap",
    "time_of_day": "sub_time",
}


def aggregate(pnls_pct: list[float], pnls_usd: list[float] | None = None) -> dict:
    """WR/PF/expectancy/net/maxDD/peor sobre una lista de desenlaces (%)."""
    n = len(pnls_pct)
    if n == 0:
        return {"n": 0, "wr": None, "pf": None, "expectancy_pct": None,
                "net_pct": None, "net_usd": None, "max_dd_pct": None,
                "worst_pct": None}
    wins = [p for p in pnls_pct if p > 0]
    losses = [p for p in pnls_pct if p < 0]
    gp, gl = sum(wins), abs(sum(losses))
    cum = peak = dd = 0.0
    for p in pnls_pct:
        cum += p
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    return {
        "n": n,
        "wr": round(100 * len(wins) / n, 1),
        "pf": round(gp / gl, 2) if gl > 0 else None,
        "expectancy_pct": round(sum(pnls_pct) / n, 4),
        "net_pct": round(sum(pnls_pct), 2),
        "net_usd": round(sum(pnls_usd), 2) if pnls_usd else None,
        "max_dd_pct": round(dd, 2),
        "worst_pct": round(min(pnls_pct), 2),
    }


def _p95(sorted_vals: list[float]) -> float | None:
    if not sorted_vals:
        return None
    k = max(0, int(round(0.95 * (len(sorted_vals) - 1))))
    return round(sorted_vals[k], 2)


def baseline_from_rows(rows: list[dict]) -> dict:
    """Línea base (in / out / total) desde la matriz, con cola p95 de |MAE|."""
    def block(sel: list[dict]) -> dict:
        m = aggregate([r["pnl_pct"] for r in sel],
                      [r.get("pnl_usd") or 0.0 for r in sel])
        maes = sorted(r["mae_pct"] for r in sel)
        if maes:
            m["mae_p95_pct"] = _p95(maes)
        maes_atr = sorted(r["mae_atr"] for r in sel
                          if r.get("mae_atr") is not None)
        if maes_atr:
            m["mae_p95_atr"] = _p95(maes_atr)
        return m

    return {
        "total": block(rows),
        "in": block([r for r in rows if r["in_sample"]]),
        "out": block([r for r in rows if not r["in_sample"]]),
    }


def selection_mask(row: dict, selection: dict) -> bool:
    """¿La fila pasa la selección? (filtros SUSTRACTIVOS — Anexo 25 §8.1.5).

    Semántica idéntica a la viva: subscores AND (cada activo exige
    subscore·100 ≥ umbral); régimen con unknown fail-open; EMA con-tendencia
    estricta (None/contra excluye).
    """
    for name, thr in (selection.get("subs") or {}).items():
        attr = _SUB_ATTR.get(name)
        if attr is None:
            return False
        if (row.get(attr) or 0) * 100 < thr:
            return False
    reg = selection.get("regime") or {}
    if reg.get("allowed"):
        value = row.get(f"regime_{reg.get('tf', '1h')}")
        if value != "unknown" and value not in reg["allowed"]:
            return False
    for key in selection.get("ema") or []:
        if (row.get("ema_with") or {}).get(key) is not True:
            return False
    return True


def lift_from_rows(rows: list[dict], selection: dict) -> dict:
    """Aplica la selección y re-agrega (in/out + % conservado + guarda n<15).

    El universo son las filas CON cobertura de barras (atr_pct presente),
    igual que en el reporte offline.
    """
    universe = [r for r in rows if r.get("atr_pct") is not None]

    def block(in_sample: bool) -> dict:
        base_sel = [r for r in universe if r["in_sample"] == in_sample]
        kept = [r for r in base_sel if selection_mask(r, selection)]
        m = aggregate([r["pnl_pct"] for r in kept])
        m["kept_pct"] = (round(100 * len(kept) / len(base_sel), 1)
                         if base_sel else None)
        return m

    out = {"in": block(True), "out": block(False)}
    out["low_n_out"] = out["out"]["n"] < LOW_N_OUT
    return out


# Grillas del cambia-desenlace (las mismas del reporte offline; el visor las
# valida — el orden intrabar solo está cacheado para estos valores).
# B5.2: TP extendida a valores nominales altos (el TP es un bracket ancho que
# TradersPost exige, no una meta — ver nominal_brackets para el modo p99).
SL_GRID = (1.5, 2.0, 2.5, 3.0, 4.0, 6.0, 8.0)
TP_GRID = (3.0, 4.0, 6.0, 8.0, 10.0, 12.0, 15.0, 20.0)

# B5.2: niveles del estudio de pullback (fuente única — lab_analyze los
# importa de aquí), extendidos hasta 10× para ver dónde tocan fondo el
# fill-rate y el desenlace.
PULLBACK_LEVELS = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0,
                   6.0, 7.0, 8.0, 9.0, 10.0)


def _joint_order(row: dict, k: float, tp: float) -> str:
    """Orden SL vs TP cuando el MAE y el MFE alcanzaron AMBOS umbrales.

    Usa los minutos al primer toque cacheados por el camino A (que caminó el
    5m intrabar): mismo minuto = misma barra → ambiguo → SL (conservador);
    sin datos de toque (cache legado) → SL (conservador, "none" del camino A).
    """
    t_sl = (row.get("t_sl_touch") or {}).get(str(k))
    t_tp = (row.get("t_tp_touch") or {}).get(str(tp))
    if t_sl is None and t_tp is None:
        return "none"
    if t_tp is None:
        return "sl"
    if t_sl is None:
        return "tp"
    if t_sl == t_tp:
        return "ambiguous_sl"
    return "sl" if t_sl < t_tp else "tp"


def resim_rows(rows: list[dict], sl_k: float | None = None,
               tp: float | None = None) -> dict:
    """Re-sim del desenlace (Anexo 25 §8.1.5) sobre la matriz — la MISMA
    lógica para el reporte offline (§2/§8/§9) y el endpoint del visor.

      SL activa ⟺ |mae%| ≥ k·atr% → −k·atr%
      TP activa ⟺ mfe% ≥ tp·atr% → +tp·atr%
      Ambos alcanzados → decide el orden de toques intrabar (cacheado).

    Devuelve bloques in/out (con sl_pct/tp_pct/ambiguous) y `outcomes`
    (desenlace por fila del universo, para la curva de equity del visor).
    """
    universe = [r for r in rows if r.get("atr_pct") is not None]
    outcomes: list[tuple[dict, float]] = []
    tags: list[str] = []
    for r in universe:
        pnl = r["pnl_pct"]
        sl_thr = sl_k * r["atr_pct"] if sl_k else None
        tp_thr = tp * r["atr_pct"] if tp else None
        sl_reach = sl_thr is not None and r["mae_pct"] >= sl_thr
        tp_reach = tp_thr is not None and r["mfe_pct"] >= tp_thr
        if sl_reach and tp_reach:
            order = _joint_order(r, sl_k, tp)
            if order == "tp":
                outcomes.append((r, tp_thr))
                tags.append("tp")
            else:
                outcomes.append((r, -sl_thr))
                tags.append("ambiguous_sl" if order == "ambiguous_sl" else "sl")
        elif sl_reach:
            outcomes.append((r, -sl_thr))
            tags.append("sl")
        elif tp_reach:
            outcomes.append((r, tp_thr))
            tags.append("tp")
        else:
            outcomes.append((r, pnl))
            tags.append("native")

    def block(in_sample: bool) -> dict:
        sel = [(r, p, t) for (r, p), t in zip(outcomes, tags)
               if r["in_sample"] == in_sample]
        m = aggregate([p for _, p, _ in sel])
        n = len(sel)
        if n:
            n_sl = sum(1 for *_, t in sel if t in ("sl", "ambiguous_sl"))
            n_tp = sum(1 for *_, t in sel if t == "tp")
            m["sl_pct"] = round(100 * n_sl / n, 1)
            m["tp_pct"] = round(100 * n_tp / n, 1)
            m["ambiguous"] = sum(1 for *_, t in sel if t == "ambiguous_sl")
        return m

    out = {"in": block(True), "out": block(False),
           "outcomes": [p for _, p in outcomes],
           # B5.1 — el tag por fila ("sl"/"ambiguous_sl"/"tp"/"native", mismo
           # orden que outcomes): la config combinada aplica las piernas
           # según CÓMO salió el trade en la etapa re-sim.
           "tags": tags}
    out["low_n_out"] = out["out"]["n"] < LOW_N_OUT
    return out


def equity_curve(pnls: list[float]) -> list[float]:
    """P&L% acumulado (curva de equity) en el orden dado (cronológico)."""
    out, cum = [], 0.0
    for p in pnls:
        cum += p
        out.append(round(cum, 4))
    return out


# ---------------------------------------------------------------------------
# B5.1 — config COMBINADA: un solo estado aplicado JUNTO, en orden documentado
# ---------------------------------------------------------------------------

def _leg_fill_min(row: dict, depth: float, approx: bool) -> float | None:
    """Minuto en que la pierna a `depth`×ATR habría llenado (None = no llena).
    Cache B4+: t_pb_touch (ventana real del estudio); legado: mae_atr aprox."""
    if depth <= 0:
        return 0.0
    if approx:
        return 0.0 if (row.get("mae_atr") or 0) >= depth else None
    return (row.get("t_pb_touch") or {}).get(str(depth))


def combined_config(rows: list[dict], selection: dict | None = None,
                    sl_k: float | None = None, tp: float | None = None,
                    legs: list | None = None) -> dict:
    """B5.1 — LA configuración combinada (las perillas interactúan; esto NO es
    la suma de efectos aislados). Orden documentado:

      1) SUSTRACTIVOS (filtros calidad + régimen + EMA, selection_mask)
         recortan el universo — un trade filtrado no se opera (ni su stop).
      2) SL/TP re-simulan el desenlace SOBRE ESE SUBCONJUNTO (resim_rows,
         orden intrabar con los toques cacheados).
      3) PIERNAS (escalonado, fills del t_pb_touch cacheado) mueven la
         entrada de lo que quedó. SL/TP ANCLADOS a la señal:
           salió SL      → la pierna a L pierde (k−L)·atr%
           salió TP      → la pierna a L gana (tp+L)·atr%  (si llenó ANTES
                           del TP — el orden usa los minutos cacheados)
           salió nativo  → pnl% + L·atr%
         Pierna sin llenar = peso sin usar (contribuye 0).

    Con una sola perilla activa degrada EXACTAMENTE al camino aislado
    (paridad: lift_from_rows / resim_rows — testeado). `outcomes` cubre el
    universo COMPLETO (0.0 en lo filtrado) para la curva comparable contra
    la base nativa; los bloques in/out agregan SOLO lo operado."""
    universe = [r for r in rows if r.get("atr_pct") is not None]
    sel = {"subs": {}, "regime": {}, "ema": [], **(selection or {})}
    kept = [r for r in universe if selection_mask(r, sel)]
    kept_ids = {id(r) for r in kept}

    rr = resim_rows(kept, sl_k=sl_k, tp=tp)
    legs_t = tuple((float(d), float(w)) for d, w in (legs or ((0.0, 1.0),)))
    has_legs = any(d > 0 for d, _ in legs_t)
    approx = has_legs and not any(r.get("t_pb_touch") for r in kept)

    def leg_outcome(r: dict, tag: str, o: float, depth: float) -> float:
        atr = r["atr_pct"]
        if tag in ("sl", "ambiguous_sl"):
            return -(sl_k - depth) * atr
        if tag == "tp":
            return (tp + depth) * atr
        return o + depth * atr

    final: list[float] = []
    fills: dict[str, int] = {str(d): 0 for d, _ in legs_t if d > 0}
    improves: list[float] = []
    for r, o, tag in zip(kept, rr["outcomes"], rr["tags"]):
        acc, improve = 0.0, 0.0
        tp_min = ((r.get("t_tp_touch") or {}).get(str(tp))
                  if tag == "tp" and tp is not None else None)
        for d, w in legs_t:
            pb_min = _leg_fill_min(r, d, approx)
            if pb_min is None:
                continue
            if tag == "tp" and d > 0 and tp_min is not None and pb_min > tp_min:
                continue                    # el TP salió antes de que llenara
            acc += w * leg_outcome(r, tag, o, d)
            if d > 0:
                fills[str(d)] += 1
                improve += w * d
        final.append(acc)
        improves.append(improve)

    def block(ins: bool) -> dict:
        vals = [v for r, v in zip(kept, final) if r["in_sample"] == ins]
        m = aggregate(vals)
        base_n = sum(1 for r in universe if r["in_sample"] == ins)
        m["kept_pct"] = (round(100 * len(vals) / base_n, 1)
                         if base_n else None)
        for key in ("sl_pct", "tp_pct", "ambiguous"):
            if key in rr["in" if ins else "out"]:
                m[key] = rr["in" if ins else "out"][key]
        return m

    out = {"in": block(True), "out": block(False)}
    out["low_n_out"] = out["out"]["n"] < LOW_N_OUT

    kept_iter = iter(final)
    out["outcomes"] = [next(kept_iter) if id(r) in kept_ids else 0.0
                       for r in universe]

    # Mini-panel "por qué suma el escalonado" (solo con piernas someras):
    # la contribución neta = net(con piernas) − net(sin piernas), MISMA etapa
    # previa (filtros + re-sim) — la mecánica hecha visible.
    if has_legs:
        def net_plain(ins):
            return round(sum(o for r, o in zip(kept, rr["outcomes"])
                             if r["in_sample"] == ins), 2)
        out["scaling"] = {
            "fills": fills,
            "avg_entry_improvement_atr": (round(sum(improves)
                                                / len(improves), 3)
                                          if improves else None),
            "net_contrib_pct": {
                "in": round((out["in"]["net_pct"] or 0) - net_plain(True), 2),
                "out": round((out["out"]["net_pct"] or 0)
                             - net_plain(False), 2),
            },
            "approx_fills": approx,
            "why": ("las piernas llenan en pullbacks someros a mejor precio, "
                    "sobre los buenos trades; las profundas casi no llenan"),
        }
    else:
        out["scaling"] = None
    out["approx_fills"] = approx
    return out


def nominal_brackets(rows: list[dict]) -> dict:
    """B5.2 — brackets NOMINALES por estrategia: TP sobre el MFE p99 y SL
    catastrófico sobre el MAE p99 (en ×ATR). El bracket es un tope ancho que
    TradersPost exige, no una meta — capar dentro del cuerpo de la
    distribución regala ganancia."""
    def p99(vals: list[float]) -> float | None:
        if not vals:
            return None
        s = sorted(vals)
        k = 0.99 * (len(s) - 1)
        f = int(k)
        c = min(f + 1, len(s) - 1)
        return round(s[f] + (s[c] - s[f]) * (k - f), 2)

    return {
        "tp_p99_atr": p99([r["mfe_atr"] for r in rows
                           if r.get("mfe_atr") is not None]),
        "sl_p99_atr": p99([r["mae_atr"] for r in rows
                           if r.get("mae_atr") is not None]),
    }


def hourly_from_rows(rows: list[dict]) -> dict[int, dict]:
    """Edge por hora (ET) sobre las filas con hora — misma función para el §3
    del reporte offline y el panel del visor."""
    covered = [r for r in rows if r.get("hour") is not None]
    out: dict[int, dict] = {}
    for h in sorted({r["hour"] for r in covered}):
        sel = [r for r in covered if r["hour"] == h]
        out[h] = {
            "in": aggregate([r["pnl_pct"] for r in sel if r["in_sample"]]),
            "out": aggregate([r["pnl_pct"] for r in sel if not r["in_sample"]]),
            "n": len(sel),
        }
    return out


# ---------------------------------------------------------------------------
# B4 — veredicto visual (heat 1–10) y supervivientes out-of-sample.
# UNA sola fuente: el reporte offline (§ supervivientes del RESUMEN) y el
# botón "mejor configuración" del visor llaman ESTAS funciones.
# ---------------------------------------------------------------------------

# Grilla de candidatos de la Fase 2 (la misma del reporte §5/§6/§7):
SUB_NAMES = ("volume_relative", "atr_normalized", "vwap_position",
             "time_of_day")
SUB_THRESHOLDS = (50, 60, 70, 80)
REGIME_GATE_DEFS = tuple(
    (f"{tf}·{label}", {"tf": tf, "allowed": allowed})
    for tf in ("1h", "4h")
    for label, allowed in (("trend", ["trending_bull", "trending_bear"]),
                           ("ranging", ["ranging"]))
)
EMA_KEYS = ("1h20", "1h50", "4h20", "4h50")


def heat_score(d_pf: float | None) -> int | None:
    """Calificación 1–10 del ΔPF (5 = neutro; ≥6 mejora, ≤4 empeora).

    Escalones simétricos: ±0.05 (neutro), ±0.25, ±0.5, ±1.0, ±2.0.
    None si no hay PF comparable (p. ej. sin pérdidas en el bloque)."""
    if d_pf is None:
        return None
    for thr, worse, better in ((2.0, 1, 10), (1.0, 2, 9), (0.5, 3, 8),
                               (0.25, 4, 7), (0.05, 4, 6)):
        if d_pf >= thr:
            return better
        if d_pf <= -thr:
            return worse
    return 5


def verdict(result: dict, deltas: dict) -> dict:
    """Veredicto por bloque para la barra de calor del visor: score 1–10 del
    ΔPF y, en out (el veredicto honesto), si el PF cruza 1.0 (sobrevive)."""
    out_pf = result["out"].get("pf")
    return {
        "in": {"score": heat_score(deltas["in"]["pf"])},
        "out": {"score": heat_score(deltas["out"]["pf"]),
                "pf": out_pf,
                "survives": (out_pf is not None and out_pf >= 1.0)},
    }


def survivors_from_lifts(
    base: dict, items: list[tuple[str, dict, dict | None]],
) -> list[dict]:
    """EL criterio de supervivencia (Anexo 25): ΔPF > 0 DENTRO y FUERA de
    muestra, ordenado por ΔPF out — NUNCA por in-sample (ahí vive el
    espejismo). items = [(label, lift, selection|None)]; n_out chico se
    marca (advierte, no descarta)."""
    out: list[dict] = []
    for label, d, selection in items:
        i, o = d["in"], d["out"]
        if None in (i["pf"], o["pf"], base["in"]["pf"], base["out"]["pf"]):
            continue
        d_in = i["pf"] - base["in"]["pf"]
        d_out = o["pf"] - base["out"]["pf"]
        if d_in > 0 and d_out > 0:
            out.append({"label": label, "selection": selection,
                        "d_in": round(d_in, 2), "d_out": round(d_out, 2),
                        "kept_in": i.get("kept_pct"), "n_out": o["n"],
                        "low_n_out": o["n"] < LOW_N_OUT})
    out.sort(key=lambda r: r["d_out"], reverse=True)
    return out


def survivor_candidates(rows: list[dict]) -> list[tuple[str, dict, dict]]:
    """(label, lift, selection) para TODA la grilla de la Fase 2 — los mismos
    candidatos (y etiquetas) del reporte offline."""
    items: list[tuple[str, dict, dict]] = []
    for name in SUB_NAMES:
        for thr in SUB_THRESHOLDS:
            sel = {"subs": {name: thr}}
            items.append((f"{name} ≥ {thr}", lift_from_rows(rows, sel), sel))
    for key, gate in REGIME_GATE_DEFS:
        sel = {"regime": gate}
        items.append((f"regime solo {key}", lift_from_rows(rows, sel), sel))
    for key in EMA_KEYS:
        sel = {"ema": [key]}
        items.append((f"EMA {key[:2]}·{key[2:]} con-tendencia",
                      lift_from_rows(rows, sel), sel))
    return items


def oos_survivors_from_rows(rows: list[dict], base: dict | None = None) -> list[dict]:
    """Supervivientes OOS directamente desde la matriz (visor B4.3)."""
    return survivors_from_lifts(base or baseline_from_rows(rows),
                                survivor_candidates(rows))


def deltas_vs_base(sel: dict, base: dict) -> dict:
    """Δ del VECTOR completo (PF/WR/exp/maxDD/net) in/out contra la base.
    Nota de signo: max_dd_pct es negativo — Δ>0 = drawdown MENOS profundo
    (mejora de riesgo); el clasificador de tradeoff (B4.2) depende de esto."""
    def d(a, b, nd=2):
        if a is None or b is None:
            return None
        return round(a - b, nd)

    def blk(name: str) -> dict:
        s, b = sel[name], base[name]
        return {"pf": d(s["pf"], b["pf"]),
                "wr": d(s["wr"], b["wr"], 1),
                "expectancy_pct": d(s["expectancy_pct"],
                                    b["expectancy_pct"], 4),
                "max_dd_pct": d(s.get("max_dd_pct"), b.get("max_dd_pct")),
                "net_pct": d(s.get("net_pct"), b.get("net_pct"))}

    return {"in": blk("in"), "out": blk("out")}


def tradeoff_read(deltas_blk: dict) -> dict:
    """B4.2 — la capa de INTERPRETACIÓN: lee el patrón de signos del Δ
    (PF, WR, maxDD) y lo traduce a una frase determinista — "el PF baja pero
    el WR se mantiene y el DD mejora → es riesgo, no calidad". Vive aquí (no
    en JS): fuente única y testeable.

    Tolerancias de "sin cambio": |ΔPF| < 0.05, |ΔWR| < 0.5pp, |ΔDD| < 0.05.
    Recuerda: Δ(max_dd_pct) > 0 = drawdown menos profundo = MENOS riesgo."""
    d_pf, d_wr = deltas_blk.get("pf"), deltas_blk.get("wr")
    d_dd = deltas_blk.get("max_dd_pct")
    if d_pf is None or d_wr is None:
        return {"pattern": None, "verdict": "sin_datos",
                "phrase": "sin datos comparables (bloque sin pérdidas o "
                          "sin trades suficientes)"}

    def sgn(v, eps):
        return 0 if v is None or abs(v) < eps else (1 if v > 0 else -1)

    pf, wr, dd = sgn(d_pf, 0.05), sgn(d_wr, 0.5), sgn(d_dd, 0.05)
    arrow = {1: "↑", 0: "=", -1: "↓"}
    # DD se rotula por PROFUNDIDAD del drawdown (dd=+1 ⇒ menos profundo ⇒ DD↓)
    pattern = f"PF{arrow[pf]} WR{arrow[wr]} DD{arrow[-dd]}"

    if pf > 0 and wr >= 0:
        v, p = "mejor", "mejor en todo — gana más por trade sin ceder acierto"
        if dd < 0:
            p += " (ojo: el drawdown se hace más profundo)"
    elif pf >= 0 and wr > 0:
        v, p = "mejor", "acierta más sin ceder PF — mejor en todo"
        if dd < 0:
            p += " (ojo: el drawdown se hace más profundo)"
    elif pf < 0 and wr < 0:
        v, p = "peor", "peor en todo — gana menos y acierta menos: descartar"
    elif pf < 0 and wr >= 0 and dd > 0:
        v, p = ("tradeoff_riesgo",
                "menos ganancia por trade, más consistente y menos riesgo — "
                "tradeoff de riesgo, no de calidad")
    elif pf < 0:
        v, p = ("tradeoff_dudoso",
                "gana menos por trade y el riesgo no mejora — "
                "tradeoff dudoso")
    elif pf > 0 and wr < 0:
        v, p = ("volatil",
                "gana más por trade pero acierta menos — más volátil")
    elif pf == 0 and wr < 0:
        v, p = "peor", "acierta menos al mismo PF — leve deterioro"
    else:
        v, p = "neutro", "sin cambio material vs base"
    return {"pattern": pattern, "verdict": v, "phrase": p}


# ---------------------------------------------------------------------------
# B4.3 — config DEFAULT recomendada por RIESGO (principio rector de NTEXECG:
# disminuir el riesgo de LuxAlgo, no maximizar ganancia). Modelo: SL ancho
# catastrófico ANCLADO a la señal + escalonado SOMERO + tamaño a riesgo fijo.
# ---------------------------------------------------------------------------

RISK_PCT_DEFAULT = 1.0            # tope duro: % de la cuenta por trade

# SLs candidatos (catastróficos anchos — cola de SL_GRID) y formas de
# escalonado SOMERO (≤0.75×; las piernas profundas están PROHIBIDAS por
# default: promedian hacia abajo en los peores trades = más riesgo).
CAT_SL_GRID = (4.0, 6.0, 8.0)
LEG_SHAPES: tuple = (
    ("entrada única (market)", ((0.0, 1.0),)),
    ("somero 50% @0 + 50% @0.5×", ((0.0, 0.5), (0.5, 0.5))),
    ("somero tercios @0/0.25×/0.5×",
     ((0.0, 1 / 3), (0.25, 1 / 3), (0.5, 1 / 3))),
    ("somero 50% @0 + 50% @0.75×", ((0.0, 0.5), (0.75, 0.5))),
)


def _leg_filled(row: dict, depth: float, approx: bool) -> bool:
    """¿La pierna a `depth`×ATR habría llenado? Con cache B4+: el toque de
    pullback cacheado (ventana real del estudio); cache legado: mae_atr
    (aprox: ignora la ventana — se marca approx_fills)."""
    if depth <= 0:
        return True
    if approx:
        return (row.get("mae_atr") or 0) >= depth
    return (row.get("t_pb_touch") or {}).get(str(depth)) is not None


def risk_sized_outcomes(rows: list[dict], sl_k: float, legs: tuple,
                        risk_pct: float = RISK_PCT_DEFAULT) -> dict:
    """Desenlaces EN % DE CUENTA con sizing a riesgo fijo (B4.3).

    Por trade: tamaño m tal que el peor caso (todas las piernas llenan y el
    stop — ANCLADO al precio de señal — pega) pierda exactamente `risk_pct`:
      m = risk_pct / Σᵢ wᵢ·(k − Lᵢ)·atr%      (la cuenta NUNCA arriesga más)
    Pierna i llena si el pullback tocó Lᵢ; desenlace por pierna:
      stopped (mae_atr ≥ k):  −(k − Lᵢ)·atr%   (mejor entrada pierde menos)
      si no:                   pnl% + Lᵢ·atr%   (mejor entrada gana más)
    Pierna sin llenar: capital sin usar (contribuye 0).
    `native_*` = mismo sizing, entrada única SIN stop (para medir cesión)."""
    universe = [r for r in rows if r.get("atr_pct")]
    approx = not any(r.get("t_pb_touch") for r in universe)
    outcomes: list[tuple[dict, float]] = []
    natives: list[tuple[dict, float]] = []
    for r in universe:
        atr = r["atr_pct"]
        worst = sum(w * (sl_k - d) for d, w in legs) * atr
        m = risk_pct / worst
        stopped = (r.get("mae_atr") or 0) >= sl_k
        acc = 0.0
        for d, w in legs:
            if not _leg_filled(r, d, approx):
                continue
            leg_pnl = (-(sl_k - d) * atr) if stopped else (r["pnl_pct"]
                                                           + d * atr)
            acc += w * leg_pnl
        outcomes.append((r, m * acc))
        natives.append((r, m * r["pnl_pct"]))

    def blk(pairs, ins):
        return aggregate([v for r, v in pairs if r["in_sample"] == ins])

    return {"in": blk(outcomes, True), "out": blk(outcomes, False),
            "native_in": blk(natives, True), "native_out": blk(natives, False),
            "outcomes": [v for _, v in outcomes], "approx_fills": approx}


def default_config_study(rows: list[dict],
                         risk_pct: float = RISK_PCT_DEFAULT) -> dict:
    """B4.3 — recomendación default por estrategia: maximiza la ganancia OUT
    (en % de cuenta) SUJETO al tope duro de riesgo (por construcción del
    sizing) y a expectancy OOS > 0 (guarda innegociable). Se elige por
    OUT-of-sample, NUNCA por in-sample (ahí vive el espejismo)."""
    candidates: list[dict] = []
    for k in CAT_SL_GRID:
        for name, legs in LEG_SHAPES:
            m = risk_sized_outcomes(rows, k, legs, risk_pct)
            exp_out = m["out"]["expectancy_pct"]
            worst = round(min(m["outcomes"]), 4) if m["outcomes"] else None
            ceded = (round(m["native_out"]["expectancy_pct"]
                           - exp_out, 4)
                     if exp_out is not None
                     and m["native_out"]["expectancy_pct"] is not None
                     else None)
            candidates.append({
                "sl_k": k,
                "legs": [{"depth": d, "weight": round(w, 4)} for d, w in legs],
                "label": f"SL {k}× anclado + {name}",
                "in": m["in"], "out": m["out"],
                "viable": exp_out is not None and exp_out > 0,
                "low_n_out": m["out"]["n"] < LOW_N_OUT,
                "approx_fills": m["approx_fills"],
                "cost": {"risk_pct": risk_pct,
                         "worst_account_pct": worst,
                         "ceded_out_pct": ceded},
            })
    candidates.sort(key=lambda c: (c["out"]["expectancy_pct"]
                                   if c["out"]["expectancy_pct"] is not None
                                   else -9e9), reverse=True)
    viable = [c for c in candidates if c["viable"]]
    return {"candidates": candidates,
            "recommended": viable[0] if viable else None,
            "none_viable": not viable,
            "risk_pct": risk_pct}
