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
