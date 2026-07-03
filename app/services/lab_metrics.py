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
SL_GRID = (1.5, 2.0, 2.5, 3.0, 4.0, 6.0, 8.0)
TP_GRID = (3.0, 4.0, 6.0)


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
           "outcomes": [p for _, p in outcomes]}
    out["low_n_out"] = out["out"]["n"] < LOW_N_OUT
    return out


def equity_curve(pnls: list[float]) -> list[float]:
    """P&L% acumulado (curva de equity) en el orden dado (cronológico)."""
    out, cum = [], 0.0
    for p in pnls:
        cum += p
        out.append(round(cum, 4))
    return out


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


def deltas_vs_base(sel: dict, base: dict) -> dict:
    """ΔPF/ΔWR/Δexpectancy in/out de una selección contra la línea base."""
    def d(a, b, nd=2):
        if a is None or b is None:
            return None
        return round(a - b, nd)

    return {
        "in": {"pf": d(sel["in"]["pf"], base["in"]["pf"]),
               "wr": d(sel["in"]["wr"], base["in"]["wr"], 1),
               "expectancy_pct": d(sel["in"]["expectancy_pct"],
                                   base["in"]["expectancy_pct"], 4)},
        "out": {"pf": d(sel["out"]["pf"], base["out"]["pf"]),
                "wr": d(sel["out"]["wr"], base["out"]["wr"], 1),
                "expectancy_pct": d(sel["out"]["expectancy_pct"],
                                    base["out"]["expectancy_pct"], 4)},
    }
