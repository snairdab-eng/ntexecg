"""LAB-3 — reconciliación Lab ↔ Motor por estrategia (auditoría, one-shot).

Estilo `lab_analyze --all-summary`: para cada llave del manifest compara el
LISTADO CRUDO del Lab (`REPORTES/lab_features_<key>.json`) contra el MASTER del
Motor (`MotorRiesgo/<clave>/manifest.json`) en tres números:

  · n_trades   — cuántas operaciones parseó cada lado
  · cobertura  — primera/última entrada (día)
  · net crudo  — PnL neto USD del listado sin config

SOLO LECTURA: reusa `routes_lab.load_cache` (misma lectura del visor) y
`routes_riesgo._motor_manifest`. NO toca lab_metrics/lab_analyze ni recomputa
nada — solo lee dos JSON pequeños por llave.

Diferencia ESPERADA (no cuenta como desalineo): el Lab excluye del ANÁLISIS los
trades sin cobertura de barras (atr_pct = None) — se reportan aparte
("N sin cobertura ATR"). Diferencias NO esperadas → ⚠: n desalineado (más allá
del filtro), cobertura distinta (ventana de export vieja, p. ej. RTY que
arrastraba 2025-08) o net crudo distinto.

Uso:  python -m scripts.lab_motor_reconcile
"""
from __future__ import annotations

# Tolerancia del net crudo (redondeos de parseo/usd_por_punto): 1 USD.
NET_TOL_USD = 1.0


def _day(ts: str | None) -> str | None:
    """Día (YYYY-MM-DD) de un timestamp ISO, o None."""
    return ts[:10] if ts else None


def reconcile_one(lab_rows: list[dict], motor: dict) -> dict:
    """Compara el listado crudo del Lab contra el master del Motor (función
    PURA — sin I/O, testeable con fixtures).

    `coincide` = n, cobertura y net crudo alineados. `atr_filtered` (trades sin
    atr_pct) es la diferencia esperada y NO afecta a `coincide`.
    """
    lab_n = len(lab_rows)
    lab_net = round(sum((r.get("pnl_usd") or 0.0) for r in lab_rows), 2)
    atr_filtered = sum(1 for r in lab_rows if r.get("atr_pct") is None)
    ets = sorted(r["entry_ts"] for r in lab_rows if r.get("entry_ts"))
    lab_desde, lab_hasta = (_day(ets[0]), _day(ets[-1])) if ets else (None, None)

    trades = (motor or {}).get("trades") or {}
    base = (motor or {}).get("linea_base_usd") or {}
    motor_n = trades.get("n")
    motor_net = base.get("net_usd")
    m_desde, m_hasta = _day(trades.get("desde")), _day(trades.get("hasta"))

    detail: list[str] = []
    n_ok = motor_n is not None and lab_n == motor_n
    if motor_n is not None and not n_ok:
        detail.append(f"n {lab_n} vs master {motor_n}")

    net_ok = (motor_net is not None
              and abs(lab_net - motor_net) <= NET_TOL_USD)
    if motor_net is not None and not net_ok:
        detail.append(f"net ${lab_net:,.0f} vs master ${motor_net:,.0f}")

    cov_ok = (lab_desde is not None and m_desde is not None
              and lab_desde == m_desde and lab_hasta == m_hasta)
    if m_desde is not None and not cov_ok:
        detail.append(
            f"cobertura {lab_desde}…{lab_hasta} vs master {m_desde}…{m_hasta}")

    return {
        "coincide": bool(n_ok and net_ok and cov_ok),
        "detail": detail,
        "lab_n": lab_n, "motor_n": motor_n,
        "lab_net": lab_net, "motor_net": motor_net,
        "lab_cov": [lab_desde, lab_hasta], "motor_cov": [m_desde, m_hasta],
        "atr_filtered": atr_filtered,
    }


def _lab_rows_for(key: str, instrument: str, load_cache) -> tuple[list | None, str | None]:
    """Filas del Lab para la llave; cae al instrumento si aún no hay caché
    por-estrategia (migración LAB-1 incompleta en disco). Devuelve
    (rows, source_key) o (None, None)."""
    cached = load_cache(key)
    if cached is not None:
        return cached[0], key
    if instrument and instrument != key:
        cached = load_cache(instrument)
        if cached is not None:
            return cached[0], instrument
    return None, None


def build_report(manifest: dict, load_cache, motor_manifest_fn,
                 clave_fn) -> list[dict]:
    """Una fila por llave del manifest con su estado de reconciliación.
    Inyectable (load_cache/motor_manifest_fn/clave_fn) para tests."""
    out: list[dict] = []
    for key, e in sorted(manifest.items(),
                         key=lambda kv: (kv[1]["instrument"], kv[0])):
        instrument = e["instrument"]
        clave = clave_fn(key, instrument)
        motor = motor_manifest_fn(clave)
        lab_rows, src = _lab_rows_for(key, instrument, load_cache)
        row = {"key": key, "instrument": instrument, "clave": clave,
               "cache_src": src}
        if motor is None:
            row["status"] = "sin_master"
        elif lab_rows is None:
            row["status"] = "sin_cache"
        else:
            rec = reconcile_one(lab_rows, motor)
            row["status"] = "coincide" if rec["coincide"] else "difiere"
            row.update(rec)
        out.append(row)
    return out


def _fmt_row(r: dict) -> str:
    mark = {"coincide": "✓", "difiere": "⚠", "sin_master": "·",
            "sin_cache": "·"}.get(r["status"], "?")
    if r["status"] == "sin_master":
        extra = "sin master del Motor"
    elif r["status"] == "sin_cache":
        extra = "sin caché del Lab"
    elif r["status"] == "coincide":
        extra = (f"n={r['lab_n']} net=${r['lab_net']:,.0f} "
                 f"[{r['lab_cov'][0]}…{r['lab_cov'][1]}]"
                 f" · {r['atr_filtered']} sin cobertura ATR")
    else:
        extra = "difiere: " + "; ".join(r["detail"])
        if r.get("atr_filtered"):
            extra += f" · {r['atr_filtered']} sin cobertura ATR (esperado)"
    src = f" (caché {r['cache_src']})" if r.get("cache_src") and \
        r["cache_src"] != r["key"] else ""
    return f" {mark}  {r['key']:32s} {r['clave']:28s} {extra}{src}"


def main() -> None:
    import app.web.routes_lab as rl
    import app.web.routes_riesgo as rr

    manifest = rl.load_manifest()
    if not manifest:
        print("Sin manifest (REPORTES/lab_manifest.json) — nada que reconciliar.")
        return
    rows = build_report(manifest, rl.load_cache, rr._motor_manifest, rr.clave_de)
    print(f"# LAB ↔ MOTOR — reconciliación del listado crudo "
          f"({len(rows)} llaves)\n")
    for r in rows:
        try:
            print(_fmt_row(r))
        except UnicodeEncodeError:                # consola cp1252
            print(_fmt_row(r).encode("ascii", "replace").decode())
    diff = [r for r in rows if r["status"] == "difiere"]
    print(f"\n{len(diff)} difieren · "
          f"{sum(1 for r in rows if r['status'] == 'coincide')} coinciden · "
          f"{sum(1 for r in rows if r['status'].startswith('sin'))} sin par")


if __name__ == "__main__":
    main()
