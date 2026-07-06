#!/usr/bin/env python3
"""mr_report — Motor de Riesgo, fase MR-3: entregables de grado publicación.

Convierte el dict de run_studies (mr_sims) en los 4 entregables del SPEC §8:
  Riesgo_<clave>_<fecha>.md         reporte legible (estructura de la
                                    referencia ES; número OOS destacado)
  configs_<clave>_<fecha>.csv       métricas de TODAS las configs probadas
  heatmap_<clave>_<fecha>.png       mapa de calor (matplotlib; color = rank
                                    por columna, PF OOS resaltado)
  recomendacion_<clave>_<fecha>.json  EL CONTRATO para el dispatch en vivo
                                    (Directiva 3.4: backstop $/pts, escalera
                                    con profundidades+distribución+nº
                                    piernas, TP nominal por lado, sizing)

Solo renderiza — TODO el cálculo vive en mr_sims (una sola fuente).
matplotlib es opcional (grupo `riesgo` en pyproject): sin él, el heatmap se
omite con aviso y el resto de entregables sale igual.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path


def _usd(v, dec: int = 0) -> str:
    if v is None:
        return "—"
    return f"${v:,.{dec}f}"


def _f(v, nd: int = 2) -> str:
    if v is None:
        return "—"
    return f"{v:.{nd}f}" if isinstance(v, float) else str(v)


def _flags_md(flags: list) -> str:
    return f" ⚠{','.join(flags)}" if flags else ""


# P1-2 (auditoría 2026-07-06) — presentación del backstop por instrumento.
# SOLO display: el cálculo del stop en L5 sigue en unidad de precio. Para FX
# la "unidad de precio" da decimales ilegibles (el yen: 0.00036 → "0 pts");
# se expresa en $/mini + ticks. Tick sizes CME estándar (full-size).
TICK_SIZE = {"ES": 0.25, "NQ": 0.25, "RTY": 0.1, "YM": 1.0,
             "GC": 0.1, "CL": 0.01, "6E": 0.00005, "6J": 0.0000005}
FX_INSTRUMENTS = {"6E", "6J"}


def fmt_stop(activo: str, pts: float | None, usd_mini: float | None) -> str:
    """Backstop legible por instrumento: FX en ticks/$ (nunca 'puntos');
    índices/commodities en pts + $."""
    if pts is None or usd_mini is None:
        return "—"
    if activo in FX_INSTRUMENTS:
        tick = TICK_SIZE.get(activo)
        ticks = round(pts / tick) if tick else None
        out = f"${usd_mini:,.0f}/mini"
        if ticks:
            out += f" = {ticks:,} ticks ({pts:.6g} en precio)"
        return out
    return f"{pts:.0f} pts = ${usd_mini:,.0f}/mini"


# ---------------------------------------------------------------------------
# Mapa de calor por columna (terciles → 🟩🟨🟥, como la referencia)
# ---------------------------------------------------------------------------

def _tercil_emoji(vals: list, mayor_mejor: bool = True) -> list[str]:
    """Emoji por valor según su tercil DENTRO de la columna (🟩 mejor)."""
    con_valor = [(i, v) for i, v in enumerate(vals) if v is not None]
    out = [""] * len(vals)
    if len(con_valor) < 2:
        return out
    orden = sorted(con_valor, key=lambda t: t[1], reverse=mayor_mejor)
    n = len(orden)
    for rank, (i, _) in enumerate(orden):
        out[i] = "🟩" if rank < n / 3 else ("🟨" if rank < 2 * n / 3 else "🟥")
    return out


# ---------------------------------------------------------------------------
# Reporte .md
# ---------------------------------------------------------------------------

def render_md(res: dict) -> str:
    meta = res["meta"]
    base = res["linea_base"]["total"]
    L: list[str] = []

    # 1 — cabecera
    L.append(f"# Riesgo — {meta['activo']} · {meta['codigo']}")
    L.append(f"### Motor de Riesgo NTEXECG · corrida {meta['fecha']} · "
             f"salida del estudio (MR-3)")
    L.append("")
    cobertura = ("✓ HOLC completo" if not meta["atr_estimado"]
                 else f"⚠ HOLC hasta {meta['holc_ultima_barra'][:10]} → "
                      f"{meta['atr_estimado']} trade(s) con ATR estimado"
                      + ("" if meta["stitch_db"] else
                         " (correr con --stitch-db en el server lo cose)"))
    hc = res["haircut"]
    hc_txt = ("sin comisiones/slippage (paridad referencia)"
              if not any(hc.values()) else
              f"haircut: comisión ${hc['comision_rt_usd']}/RT · "
              f"slip {hc['slip_pts']} pts · gap {hc['gap_pts']} pts")
    L.append(f"Fuente: `master.csv` (sha `{meta['master_sha256'][:12]}…`) · "
             f"**{meta['n_trades_listado']} trades** · "
             f"universo con ATR: {res['universo']['n']} · {hc_txt}.")
    L.append(f"{cobertura} · split OOS {int(meta['oos']*100)}% · "
             f"$/punto {meta['usd_por_punto']} · "
             f"rejillas `{meta['grids_version']}` · "
             f"motor `{meta['motor_commit']}`.")
    if res["universo"]["n"] < 80:
        L.append("⚠ **N bajo** (<80 trades): robustez frágil (SPEC §9.4).")
    L.append("")

    # 2 — línea base
    L.append("---")
    L.append("")
    L.append("## 1. LÍNEA BASE — CRUDO (la señal sin gestión · 1 mini @ "
             "señal · scripted exit)")
    L.append("*Estos números son del listado CRUDO: la señal sola, sin "
             "backstop/escalera/TP. El crudo puede decaer fuera de muestra; "
             "la comparación crudo↔con-config vive en §3/§4.*")
    L.append("")
    L.append("| Métrica | Valor |")
    L.append("|---|---:|")
    L.append(f"| **Total PnL** | **{_usd(base['net_usd'], 2)}** |")
    L.append(f"| Trades | {base['n']} |")
    L.append(f"| Operaciones rentables | {base['ganadores']} "
             f"(**WinRate {base['wr_pct']}%**) |")
    L.append(f"| **Profit Factor** | **{base['pf']}** |")
    L.append(f"| Ganancia bruta / Pérdida bruta | "
             f"{_usd(base['ganancia_bruta_usd'])} / "
             f"{_usd(base['perdida_bruta_usd'])} |")
    L.append(f"| **Max Drawdown** | **{_usd(base['max_dd_usd'])}** "
             f"({base['max_dd_pct_hwm']}% del pico de equity) |")
    L.append(f"| Peor trade | **{_usd(base['peor_trade_usd'], 2)}** |")
    score_base = res["backstop"].get("score_base")
    L.append(f"| PnL / DD | {_f(score_base)} |")
    L.append("")
    L.append("*(Escala: 1 mini = 10 micros. Todas las configs despliegan "
             "el MISMO tamaño total = 10 micros, comparables 1:1.)*")
    L.append("")

    # 3 — control de riesgo
    L.append("---")
    L.append("")
    L.append("## 2. ANÁLISIS DE CONTROL DE RIESGO")
    L.append("")
    g = res["mae_floor"]["ganadoras_mae_atr"]
    L.append(f"**a) Suelo del SL (MAE→ATR de las ganadoras):** "
             f"mediana {g['mediana']}× · media {g['media']}× · "
             f"p90 {g['p90']}× · p95 {g['p95']}× · máx {g['max']}×.")
    L.append(f"→ {res['mae_floor']['veredicto']}.")
    L.append("")
    L.append("| SL duro k×ATR | Δnet | PF | ganadoras cortadas | estado |")
    L.append("|---:|---:|---:|---:|---|")
    for r in res["mae_floor"]["sl_duro_x_atr"]:
        L.append(f"| {r['k_atr']}× | {_usd(r['delta_net_usd'])} | "
                 f"{_f(r['pf'])} | {_f(r['ganadoras_cortadas_pct'], 1)}% | "
                 f"{r['estado']} |")
    L.append("")

    b = res["backstop"]["optimo"]
    if b:
        L.append(f"**b) Backstop catastrófico ($ fijo) — el airbag:** "
                 f"óptimo **{_usd(b['backstop_usd'])} = "
                 f"{b['backstop_pts']:.0f} pts ≈ "
                 f"{b['x_atr_mediana']}×ATR mediano**. "
                 f"Toca {b['tocados']} de {res['universo']['n']} trades · "
                 f"Δnet {_usd(b['delta_net_usd'])} · "
                 f"MaxDD {b['delta_dd_pct']}% · "
                 f"peor trade topado en {_usd(b['peor_trade_usd'])}.")
        gs = b["peor_con_gap_usd"]
        L.append(f"→ **Estrés de gap** (el hueco puede atravesar el stop): "
                 f"peor trade con gap 0/10/25 pts = "
                 f"{_usd(gs['0.0'])} / {_usd(gs['10.0'])} / "
                 f"{_usd(gs['25.0'])}. El número honesto no es rosa.")
    else:
        L.append("**b) Backstop:** ningún nivel del grid supera el score "
                 "de la base — revisar el listado.")
    L.append("")
    L.append("| Backstop | pts | ×ATR med | toca | Δnet | ΔDD% | "
             "peor | net/DD |")
    L.append("|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in res["backstop"]["grid"]:
        marca = " ◀ óptimo" if b and r["backstop_usd"] == b["backstop_usd"] \
            else ""
        L.append(f"| {_usd(r['backstop_usd'])}{marca} | "
                 f"{r['backstop_pts']:.0f} | {r['x_atr_mediana']} | "
                 f"{r['tocados']} | {_usd(r['delta_net_usd'])} | "
                 f"{_f(r['delta_dd_pct'], 1)} | "
                 f"{_usd(r['peor_trade_usd'])} | {_f(r['score_net_dd'])} |")
    L.append("")

    ls = res["ls"]
    L.append(f"**c) Asimetría Long/Short:** **{ls['lectura']}**.")
    L.append("")
    L.append("| Lado | n | Net | PF | WinRate | Peor | give-backs ≥3×ATR |")
    L.append("|---|---:|---:|---:|---:|---:|---:|")
    for lado in ("long", "short"):
        m = ls[lado]
        L.append(f"| **{lado}** | {m['n']} | {_usd(m['net_usd'])} | "
                 f"**{_f(m['pf'])}** | {m['wr_pct']}% | "
                 f"{_usd(m['peor_trade_usd'])} | "
                 f"{m['giveback_perdedores_3atr']} |")
    L.append("")

    tp = res["tp"]
    L.append("**d) TP nominal — por ENCIMA de donde cierra LuxAlgo** "
             "(que cierre LuxAlgo; el TP solo satisface TradersPost):")
    L.append("")
    L.append("| Lado | cierra p50 | p95 | p99 | **TP nominal** | "
             "dispararía | en la mesa (MFE−salida) |")
    L.append("|---|---:|---:|---:|---:|---:|---:|")
    for lado, d in tp["por_lado"].items():
        c = d["cierre_atr"]
        L.append(f"| {lado} | {_f(c['p50'])}× | {_f(c['p95'])}× | "
                 f"{_f(c['p99'])}× | **{_f(d['tp_nominal_atr'], 1)}×ATR** | "
                 f"{_f(d['tp_nominal_dispararia_pct'], 1)}% | "
                 f"{_usd(d['en_la_mesa_usd'])} |")
    tm = tp["tp_meta_mejor"]
    if tm:
        L.append("")
        L.append(f"*TP-meta INFORMATIVO (cuánto habría en la mesa, evaluado "
                 f"sobre el stack): óptimo L{tm['tp_long']}/S{tm['tp_short']} "
                 f"→ net {_usd(tm['net_usd'])}, PF OOS {_f(tm['pf_out'])}. "
                 f"NO es la recomendación — la recomendación honra «que "
                 f"cierre LuxAlgo».*")
    L.append("")
    L.append("**Descartados (no aportan — no se recomiendan):** "
             + " · ".join(res["descartados_por_diseno"]) + ".")
    L.append("")

    corte = res.get("corte_fills")
    if corte:
        L.append(f"**e) Corte de tiempo de llenado (cancel_after "
                 f"{corte['cancel_after_s']:.0f}s — máx duro de TradersPost "
                 f"3600s):** en producción la orden límite se CANCELA a los "
                 f"cancel_after segundos; una pierna cuenta como llena solo "
                 f"si el pullback la tocó a tiempo (t_pb_touch del Lab). "
                 f"**Los fills con corte son más bajos que el «alguna vez "
                 f"llena» — a propósito: son los reales.**")
        L.append("")
        L.append("| nivel ×ATR | fill% sin corte | **fill% con corte** | "
                 "retención | t med | t p90 | cancel_after sugerido |")
        L.append("|---:|---:|---:|---:|---:|---:|---:|")
        for r in corte["niveles"]:
            L.append(f"| {r['nivel_atr']}× | "
                     f"{_f(r['fill_sin_corte_pct'], 1)}% | "
                     f"**{_f(r['fill_con_corte_pct'], 1)}%** | "
                     f"{_f(r['retencion'])} | "
                     f"{_f(r['t_med_min'], 0)}m | {_f(r['t_p90_min'], 0)}m | "
                     f"{r['cancel_after_sugerido_s'] or '—'}s |")
        L.append("")
        L.append(f"→ **Tope natural de profundidad: "
                 f"{corte['tope_natural_atr']}×ATR** (el nivel más hondo "
                 f"con fill ≥10% y retención ≥50% dentro del corte). "
                 f"{corte['n_sin_datos_tiempo']} trade(s) sin datos de "
                 f"tiempo usan el MAE (optimista, marcado).")
        comp = res.get("comparativa_sin_corte")
        if comp and comp["top_net"]:
            L.append("")
            L.append("*Comparativa SIN corte (el modelo original, solo "
                     "estudio — la recomendación sale del barrido CON "
                     "corte):* " + " · ".join(
                         f"{t['nombre']} (net {_usd(t['net_usd'])}, "
                         f"score {_f(t['score'])})"
                         for t in comp["top_net"][:3])
                     + f" · líder score sin corte: "
                       f"{comp['lider_score_sin_corte']}.")
        L.append("")

    # 4 — configs (mapa de calor en tabla)
    L.append("---")
    L.append("")
    L.append("## 3. CONFIGURACIONES — candidatas (10 micros, vs línea base)")
    L.append("Ordenadas por score (net/maxDD). 🟩 mejor · 🟨 medio · "
             "🟥 peor por columna. Barrido completo en `configs_*.csv` "
             f"({len(res['configs'])} configs).")
    L.append("")
    filas = _filas_candidatas(res)
    cols = {
        "net": ([f["net"] for f in filas], True),
        "pf_oos": ([f["pf_oos"] for f in filas], True),
        "dd": ([f["dd"] for f in filas], False),
        "peor": ([f["peor"] for f in filas], True),   # menos negativo mejor
        "part": ([f["part"] for f in filas], True),
        "wr": ([f["wr"] for f in filas], True),
    }
    emo = {k: _tercil_emoji(v, mayor) for k, (v, mayor) in cols.items()}
    L.append("| # | Config | Net $ | **PF OOS (con config)** | Max DD | "
             "Peor | Part% | WR% | gate |")
    L.append("|--:|---|---:|---:|---:|---:|---:|---:|---|")
    for i, f in enumerate(filas):
        L.append(
            f"| {i + 1} | {f['nombre']}{_flags_md(f['flags'])} | "
            f"{emo['net'][i]}{_usd(f['net'])} | "
            f"{emo['pf_oos'][i]}**{_f(f['pf_oos'])}** | "
            f"{emo['dd'][i]}{_usd(f['dd'])} | "
            f"{emo['peor'][i]}{_usd(f['peor'])} | "
            f"{emo['part'][i]}{_f(f['part'], 1)} | "
            f"{emo['wr'][i]}{_f(f['wr'], 1)} | {f['gate']} |")
    L.append(f"| — | CRUDO (señal, sin gestión) | {_usd(base['net_usd'])} | "
             f"{_f(_pf_base_oos(res))} | {_usd(base['max_dd_usd'])} | "
             f"{_usd(base['peor_trade_usd'])} | 100.0 | "
             f"{_f(base['wr_pct'], 1)} | — |")
    L.append("")

    # 5 — robustez
    rob = res.get("robustez")
    if rob:
        L.append("---")
        L.append("")
        L.append("## 4. ROBUSTEZ (walk-forward — el número que manda es "
                 "el OOS)")
        L.append("Dos columnas EXPLÍCITAS para no confundir: **PF OOS "
                 "crudo (señal)** = la señal sola en ese bloque (puede "
                 "decaer fuera de muestra) vs **PF OOS con config** = la "
                 "misma ventana CON la gestión puesta. ΔPF = con config − "
                 "crudo, del MISMO bloque; H1/H2 = mitades temporales.")
        L.append("")
        elegido_nombre = (rob.get("elegido") or {}).get("nombre")
        L.append("| Config | part% | PF in | PF OOS crudo (señal) | "
                 "**PF OOS con config** | ΔPF OOS | PF H1 | PF H2 | "
                 "veredicto |")
        L.append("|---|---:|---:|---:|---:|---:|---:|---:|---|")
        for t in rob["tabla"]:
            bl = t["bloques"]
            marca = (" ◀ **ELEGIDO**" if t["nombre"] == elegido_nombre
                     else "")
            L.append(f"| {t['nombre']}{_flags_md(t['flags'])}{marca} | "
                     f"{_f(t['participacion_pct'], 1)} | "
                     f"{_f(bl['in']['pf'])} | {_f(bl['out']['pf_base'])} | "
                     f"**{_f(bl['out']['pf'])}** | "
                     f"{_f(bl['out']['delta_pf'])} | {_f(bl['h1']['pf'])} | "
                     f"{_f(bl['h2']['pf'])} | {t['veredicto']} |")
        L.append("")

        h2h = rob.get("head_to_head")
        if h2h:
            L.append("### 4.1 Head-to-head — los dos líderes del BARRIDO "
                     "(comparación específica, NO la elección)")
            L.append("")
            # si el elegido no es ninguno de los dos líderes, entra como
            # columna rotulada — que no parezca contradicción
            cols = [("líder por NET", h2h["lider_net"]),
                    ("líder por SCORE", h2h["lider_score"])]
            if (elegido_nombre
                    and elegido_nombre not in (h2h["lider_net"]["nombre"],
                                               h2h["lider_score"]["nombre"])):
                fila = next((t for t in rob["tabla"]
                             if t["nombre"] == elegido_nombre), None)
                if fila:
                    cols.append(("**ELEGIDO** (mejor global)", fila))
            L.append("| | " + " | ".join(lbl for lbl, _ in cols) + " |")
            L.append("|---|" + "---|" * len(cols))
            L.append("| config | "
                     + " | ".join(t["nombre"] for _, t in cols) + " |")
            for key, label in (("out", "**PF OOS**"), ("h1", "PF H1"),
                               ("h2", "PF H2")):
                L.append(f"| {label} | " + " | ".join(
                    f"{_f(t['bloques'][key]['pf'])} "
                    f"(Δ{_f(t['bloques'][key]['delta_pf'])})"
                    for _, t in cols) + " |")
            L.append("| net OOS | " + " | ".join(
                _usd(t["bloques"]["out"]["net_usd"]) for _, t in cols)
                + " |")
            L.append("| maxDD OOS | " + " | ".join(
                _usd(t["bloques"]["out"]["max_dd_usd"]) for _, t in cols)
                + " |")
            L.append("| veredicto | "
                     + " | ".join(t["veredicto"] for _, t in cols) + " |")
            L.append("")
        if elegido_nombre:
            L.append(f"**ELEGIDO del estudio: {elegido_nombre}** — mejor "
                     f"score (net/maxDD) entre TODOS los candidatos "
                     f"validados por el walk-forward (barrido + referencia "
                     f"+ señal), nunca por in-sample. El head-to-head de "
                     f"arriba compara específicamente los dos líderes del "
                     f"barrido — no es la elección. Ver §5.")
            L.append("")

        estres = rob.get("estres_pierna_profunda")
        if estres:
            c = estres["contribucion"]
            L.append(f"### 4.2 Estrés de la pierna profunda — "
                     f"{estres['config']}")
            L.append(f"Pierna de **{estres['micros']} micros @ "
                     f"{estres['depth_atr']}×ATR**: la llenan "
                     f"**{estres['n_fills']} trades** "
                     f"({estres['fills_por_bloque']['in']} in / "
                     f"{estres['fills_por_bloque']['out']} out · "
                     f"H1 {estres['fills_por_bloque']['h1']} / "
                     f"H2 {estres['fills_por_bloque']['h2']})"
                     f"{_flags_md(estres['flags'])}.")
            L.append(f"Contribución de la pierna: "
                     f"**{_usd(c['total_usd'])}** en {estres['n_fills']} "
                     f"fills ({c['ganadores']} ganadores / "
                     f"{c['perdedores']} perdedores · mediana "
                     f"{_usd(c['mediana_usd'])} · rango "
                     f"{_usd(c['peor_usd'])} → {_usd(c['mejor_usd'])}).")
            L.append("")
            L.append("| bloque | fills | PF con pierna | PF sin pierna | "
                     "net con | net sin |")
            L.append("|---|---:|---:|---:|---:|---:|")
            for name in ("in", "out", "h1", "h2"):
                p = estres["pf_por_bloque_con_vs_sin"][name]
                L.append(f"| {name} | "
                         f"{estres['fills_por_bloque'][name]} | "
                         f"{_f(p['pf_con'])} | {_f(p['pf_sin'])} | "
                         f"{_usd(p['net_con'])} | {_usd(p['net_sin'])} |")
            L.append("")

    # 6 — reconciliación
    rec = res.get("reconciliacion_fills")
    if rec:
        L.append(f"*Reconciliación fills escalera↔pullback del Lab: Δ máx "
                 f"{_f(rec['max_delta_somero_pp'], 1)} pp en niveles someros "
                 f"(≤2×ATR) — coinciden; en profundos la ventana de 180 min "
                 f"del Lab corta fills tardíos.*")
        L.append("")

    # 7 — recomendación
    L.append("---")
    L.append("")
    L.append("## 5. RECOMENDACIÓN")
    reco = res.get("recomendacion")
    if reco:
        piernas = " + ".join(f"{p['micros']} MES @ {p['depth_atr']:g}×ATR"
                             for p in reco["escalera"]["piernas"])
        conf = reco["confianza_oos"]
        L.append(f"- **Config operativa:** {reco['config']} — {piernas} "
                 f"(anclada al precio de señal, total "
                 f"{reco['escalera']['total_micros']} micros).")
        if reco["backstop"]:
            L.append(f"- **Airbag imprescindible:** backstop "
                     f"**{fmt_stop(meta['activo'], reco['backstop']['pts'], reco['backstop']['usd_por_mini'])}** "
                     f"desde la señal "
                     f"({_usd(reco['backstop']['usd_por_micro'])}/micro) — "
                     f"stop de PRECIO FIJO, no ×ATR.")
        tpn = reco["tp_nominal_atr"] or {}
        L.append(f"- **TP nominal (que cierre LuxAlgo):** "
                 f"L {_f(tpn.get('long'), 1)}×ATR / "
                 f"S {_f(tpn.get('short'), 1)}×ATR — por encima del p99 "
                 f"del cierre; casi nunca dispara.")
        L.append(f"- **Número de confianza: PF OOS con config "
                 f"{_f(conf['pf_out'])}** (ΔPF OOS "
                 f"{_f(conf['delta_pf_out'])} vs el CRUDO del mismo bloque) · "
                 f"{conf['veredicto']}{_flags_md(conf['flags'])}. "
                 f"*{conf['nota']}.*")
        L.append(f"- **Gestión por lado:** {reco['gestion_por_lado']}.")
        if reco.get("cancel_after_seconds") is not None:
            L.append(f"- **cancel_after coherente con el ladder: "
                     f"{reco['cancel_after_seconds']}s** (p90 del toque de "
                     f"la pierna más profunda incluida, estimador NX-17, "
                     f"tope 3600) → `entry_reserve_timeout_seconds`.")
        if reco.get("corte"):
            L.append(f"- *{reco['corte']['nota']} — corte del estudio: "
                     f"{reco['corte']['cancel_after_s_estudio']:.0f}s; tope "
                     f"natural {reco['corte']['tope_natural_atr']}×ATR.*")
        L.append(f"- Sizing: **tamaño fijo** (10 micros = 1 mini) — sin "
                 f"equity. El operador aplica/afina en la pestaña de config "
                 f"de la estrategia; `recomendacion_*.json` es el puente.")
    else:
        L.append("- **Sin recomendación:** ninguna config quedó VALIDADA "
                 "por el walk-forward (o no hubo backstop óptimo). "
                 "La línea base manda.")
    L.append("")
    L.append("---")
    L.append(f"*Reproducibilidad: master sha `{meta['master_sha256'][:12]}…` "
             f"· HOLC {meta['holc_ultima_barra'][:16]}"
             f"{' +stitch' if meta['stitch_db'] else ''} · rejillas "
             f"`{meta['grids_version']}` · motor `{meta['motor_commit']}` · "
             f"estudio `estudios_{meta['fecha']}.json`.*")
    return "\n".join(L) + "\n"


def _pf_base_oos(res: dict) -> float | None:
    rob = res.get("robustez")
    if rob and rob["tabla"]:
        return rob["tabla"][0]["bloques"]["out"]["pf_base"]
    return res["linea_base"]["out"].get("pf")


def _filas_candidatas(res: dict) -> list[dict]:
    """Filas del §3 y del heatmap: las candidatas del walk-forward (ya
    curadas por robustez_study), ordenadas por score desc."""
    rob = res.get("robustez")
    if not rob:
        return []
    por_nombre = {c["nombre"]: c for c in res["configs"]}
    filas = []
    for t in sorted(rob["tabla"], key=lambda t: -(t["score"] or -9e18)):
        c = por_nombre[t["nombre"]]
        filas.append({
            "nombre": t["nombre"],
            "net": c["total"].get("net_usd"),
            "pf_oos": t["bloques"]["out"]["pf"],
            "dd": c["total"].get("max_dd_usd"),
            "peor": c["total"].get("peor_trade_usd"),
            "part": c["participacion_pct"],
            "wr": c["total"].get("wr_pct"),
            "gate": c["gate"]["estado"],
            "flags": t["flags"],
            "score": t["score"],
        })
    return filas


# ---------------------------------------------------------------------------
# configs_*.csv (todas las configs probadas)
# ---------------------------------------------------------------------------

_CSV_COLS = ("nombre", "n_piernas", "piernas", "backstop_usd", "tp_long_atr",
             "tp_short_atr", "solo_lado", "etiquetas", "participacion_pct",
             "n_participados", "n_participados_out", "net_usd", "pf",
             "wr_pct", "max_dd_usd", "peor_trade_usd", "pf_in", "pf_out",
             "score", "estado_gate", "flags")


def write_csv(res: dict, path: Path) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(_CSV_COLS)
        for c in res["configs"]:
            tp = c["tp_por_lado_atr"] or {}
            w.writerow([
                c["nombre"], c["n_piernas"],
                " + ".join(f"{round(l['peso'] * 10)}@{l['depth_atr']:g}x"
                           for l in c["legs"]),
                c["backstop_usd"], tp.get("long"), tp.get("short"),
                c["solo_lado"] or "", "|".join(c["etiquetas"]),
                c["participacion_pct"], c["n_participados"],
                c["n_participados_out"], c["total"].get("net_usd"),
                c["total"].get("pf"), c["total"].get("wr_pct"),
                c["total"].get("max_dd_usd"),
                c["total"].get("peor_trade_usd"), c["in"].get("pf"),
                c["out"].get("pf"), c["gate"]["score"],
                c["gate"]["estado"], "|".join(c["gate"]["flags"]),
            ])


# ---------------------------------------------------------------------------
# heatmap_*.png (matplotlib — color con significado, PF OOS resaltado)
# ---------------------------------------------------------------------------

def write_heatmap(res: dict, path: Path) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle
    except ImportError:
        print("⚠ matplotlib no disponible — heatmap omitido "
              "(pip install -e .[riesgo])")
        return False

    filas = _filas_candidatas(res)
    if not filas:
        return False
    base = res["linea_base"]["total"]
    filas = filas + [{
        "nombre": "CRUDO (señal, sin gestión)",
        "net": base.get("net_usd"), "pf_oos": _pf_base_oos(res),
        "dd": base.get("max_dd_usd"), "peor": base.get("peor_trade_usd"),
        "part": 100.0, "wr": base.get("wr_pct"), "gate": "—", "flags": [],
        "score": None,
    }]
    cols = [("net", "Net $", True, "${:,.0f}"),
            ("pf_oos", "PF OOS ★", True, "{:.2f}"),
            ("dd", "Max DD $", False, "${:,.0f}"),
            ("peor", "Peor $", True, "${:,.0f}"),
            ("part", "Part %", True, "{:.1f}"),
            ("wr", "WR %", True, "{:.1f}")]

    nrows, ncols = len(filas), len(cols)
    fig, ax = plt.subplots(figsize=(12.5, 0.46 * nrows + 2.2), dpi=150)
    cmap = plt.get_cmap("RdYlGn")

    for j, (key, _lbl, mayor, fmt) in enumerate(cols):
        vals = [f[key] for f in filas]
        con = [v for v in vals if v is not None]
        lo, hi = (min(con), max(con)) if con else (0, 1)
        for i, f in enumerate(filas):
            v = f[key]
            if v is None:
                color, txt = "#dddddd", "—"
            else:
                t = 0.5 if hi == lo else (v - lo) / (hi - lo)
                if not mayor:
                    t = 1.0 - t
                color = cmap(0.12 + 0.76 * t)
                txt = fmt.format(v)
            es_base = i == nrows - 1
            ax.add_patch(Rectangle((j, nrows - 1 - i), 1, 1,
                                   facecolor="#f2f2f2" if es_base else color,
                                   edgecolor="white", linewidth=1.5))
            ax.text(j + 0.5, nrows - 1 - i + 0.5, txt, ha="center",
                    va="center", fontsize=8.5,
                    fontweight="bold" if key == "pf_oos" else "normal",
                    color="#333333")

    # resaltar la columna del número de confianza (PF OOS)
    ax.add_patch(Rectangle((1, 0), 1, nrows, fill=False,
                           edgecolor="#1a1a1a", linewidth=2.2))

    for i, f in enumerate(filas):
        marca = " ⚠" if f["flags"] else ""
        peso = "bold" if (res.get("recomendacion")
                          and f["nombre"] == res["recomendacion"]["config"]) \
            else "normal"
        ax.text(-0.15, nrows - 1 - i + 0.5, f["nombre"][:44] + marca,
                ha="right", va="center", fontsize=8.5, fontweight=peso)
    for j, (_k, lbl, _m, _f2) in enumerate(cols):
        ax.text(j + 0.5, nrows + 0.18, lbl, ha="center", va="bottom",
                fontsize=9.5, fontweight="bold")

    meta = res["meta"]
    ax.set_title(
        f"Motor de Riesgo — {meta['activo']} · {meta['codigo']} · "
        f"{meta['fecha']}\n"
        f"candidatas del walk-forward (10 micros = 1 mini, comparable 1:1 "
        f"con la base) · color = escala por columna (verde mejor) · "
        f"★ = número de confianza (OOS, no in-sample)",
        fontsize=10, pad=28, loc="left")
    ax.set_xlim(-6.2, ncols)
    ax.set_ylim(-0.2, nrows + 0.9)
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return True


# ---------------------------------------------------------------------------
# recomendacion_*.json — EL CONTRATO estudio → dispatch en vivo
# ---------------------------------------------------------------------------

def write_recomendacion(res: dict, path: Path) -> None:
    meta = res["meta"]
    reco = res.get("recomendacion")
    doc = {
        "version": 1,
        "clave": meta["clave"],
        "activo": meta["activo"],
        "codigo": meta["codigo"],
        "fecha": meta["fecha"],
        "instrumento": {"usd_por_punto": meta["usd_por_punto"],
                        "micros_por_mini": 10},
        "sizing": {
            "modo": "tamano_fijo",
            "sin_equity": True,
            "nota": ("el estudio corre a 10 micros = 1 mini (comparable "
                     "1:1 con la base); el operador aplica/afina en la "
                     "pestaña de config de la estrategia"),
        },
        "tp_politica": ("nominal por ENCIMA del cierre de LuxAlgo (que "
                        "cierre LuxAlgo; el TP solo satisface TradersPost)"),
        "fail_closed": ("sin ATR para las piernas → entrada única; el "
                        "backstop de precio fijo SIEMPRE se puede calcular "
                        "→ toda entrada tiene stop"),
        "descartados": res["descartados_por_diseno"],
        "fuente": {
            "estudio": f"estudios_{meta['fecha']}.json",
            "master_sha256": meta["master_sha256"],
            "holc_ultima_barra": meta["holc_ultima_barra"],
            "stitch_db": meta["stitch_db"],
            "grids_version": meta["grids_version"],
            "motor_commit": meta["motor_commit"],
        },
    }
    if reco:
        doc.update({
            "config": reco["config"],
            "escalera": reco["escalera"],
            "backstop": reco["backstop"],
            "tp_nominal_atr": reco["tp_nominal_atr"],
            "confianza_oos": reco["confianza_oos"],
            "metricas": reco["metricas"],
            "gestion_por_lado": reco["gestion_por_lado"],
            # corte de fills (cancel_after): el ladder recomendado y su
            # entry_reserve_timeout_seconds coherente salen del barrido CON
            # corte — los fills reales de producción.
            "cancel_after_seconds": reco.get("cancel_after_seconds"),
            "corte": reco.get("corte"),
        })
    else:
        doc["sin_recomendacion"] = True
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False),
                    encoding="utf-8")


# ---------------------------------------------------------------------------
# Orquestador de entregables
# ---------------------------------------------------------------------------

def generar_entregables(res: dict, runs_dir: Path) -> dict[str, Path | None]:
    """Escribe los 4 entregables del SPEC §8 en runs/. Devuelve las rutas
    (heatmap None si falta matplotlib)."""
    meta = res["meta"]
    stem = f"{meta['clave']}_{meta['fecha']}"
    md = runs_dir / f"Riesgo_{stem}.md"
    md.write_text(render_md(res), encoding="utf-8")
    csv_p = runs_dir / f"configs_{stem}.csv"
    write_csv(res, csv_p)
    png = runs_dir / f"heatmap_{stem}.png"
    ok_png = write_heatmap(res, png)
    reco = runs_dir / f"recomendacion_{stem}.json"
    write_recomendacion(res, reco)
    return {"md": md, "csv": csv_p, "png": png if ok_png else None,
            "recomendacion": reco}
