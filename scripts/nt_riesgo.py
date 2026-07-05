#!/usr/bin/env python3
"""nt_riesgo — Motor de Riesgo NTEXECG (CLI). Fase MR-1: ingesta + persistencia.

Un motor único que recibe el listado de operaciones de una estrategia LuxAlgo,
lo integra (SOBRESCRIBE el master — cada export es el histórico completo) y
persiste todo lo necesario para recrear cualquier estudio idénticamente
(CONTRATO/Motor_Riesgo_SPEC.md + Directiva del arquitecto).

REUSA el núcleo YA PROBADO del Lab (Directiva 1 — no reconstruir):
  - parser LuxAlgo, carga HOLC, validación TZ bloqueante, ATR(14) vivo y
    costura de la cola desde Postgres: `scripts/lab_analyze.py`
  - agregación (WR/PF/net/maxDD/peor — unit-agnóstica): `lab_metrics.aggregate`
  - instrumento desde el nombre del CSV: `scripts/lab_manifest.csv_instrument`

Comandos (MR-1):
  integrar <export.csv> --codigo <codigo> [--activo SYM] [--stitch-db]
                        [--fecha YYYY-MM-DD]
      Sobrescribe master, archiva snapshot inmutable, enriquece con ATR,
      escribe manifest reforzado y CUADRA la línea base al dólar contra el
      `PyG acumuladas USD` final del export (bloqueante si no coincide).
  calcular <clave> [--stitch-db] [--oos 0.3] [--fecha] [--comision]
                   [--slip-pts] [--gap-pts]                       (MR-2)
      Corre los estudios de riesgo (scripts/mr_sims.py: backstop sweep,
      escalera por MAE con alta participación, TP nominal por encima del
      cierre de LuxAlgo, asimetría L/S, gating, reconciliación de fills
      con el pullback del Lab) y persiste runs/estudios_<fecha>.json.
  estado [<clave>]
      Resumen por carpeta MotorRiesgo/: nº trades, rango, cobertura HOLC,
      última integración, última corrida.

`recrear` llega en MR-4 (fases del SPEC §10).

Uso (NTDEV):  .venv\\Scripts\\python.exe -m scripts.nt_riesgo integrar ...
Uso (server): .venv/bin/python -m scripts.nt_riesgo integrar ... --stitch-db
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import re
import shutil
import statistics
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

# ── Núcleo del Lab (una sola fuente de verdad — Directiva 1) ──
from app.services.lab_metrics import PULLBACK_LEVELS, SL_GRID, TP_GRID
from app.services.market_data_service import _calc_atr
from scripts.lab_analyze import (
    _ATR_LOOKBACK,
    _ATR_PERIOD,
    _MIN_SANITY,
    _f,
    Trade,
    detect_tz_offset,
    enrich_with_bars,
    load_holc,
    parse_luxalgo_csv,
    pullback_study,
    split_in_out,
    stitch_from_db,
)
from scripts.lab_manifest import csv_instrument
# ── Simuladores MR-2 (núcleo puro del motor — scripts/mr_sims.py) ──
from scripts.mr_sims import HaircutCfg, from_trades, metrics_usd, run_studies

MOTOR_DIR = Path("MotorRiesgo")
MANIFEST_VERSION = 1

# $/punto conocidos por instrumento (SPEC §6) — se VERIFICAN contra el export
# (`Tamaño de la posición (valor)` / precio·cant); el inferido manda si el
# instrumento no está en la tabla (6E/6J y futuros nuevos).
USD_PER_POINT_KNOWN = {"ES": 50.0, "NQ": 20.0, "RTY": 50.0, "YM": 5.0,
                       "GC": 100.0, "CL": 1000.0}

_FECHA_EN_NOMBRE = re.compile(r"_(\d{4}-\d{2}-\d{2})_")


# ---------------------------------------------------------------------------
# Lectura del pie del export: PnL acumulado final (el cuadre al dólar) y
# $/punto inferido de `Tamaño de la posición (valor)` (SPEC §6)
# ---------------------------------------------------------------------------

def read_export_footer(path: Path) -> tuple[float | None, float | None]:
    """(pnl_acumulado_final, usd_por_punto_inferido) del export crudo."""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    best_num = -1
    cum: float | None = None
    ratios: list[float] = []
    for r in rows:
        num = r.get("Trade number")
        if not num:
            continue
        n = int(num)
        c = _f(r.get("PyG acumuladas USD", ""))
        if c is not None and n > best_num:
            best_num, cum = n, c
        if (r.get("Tipo") or "").strip().lower().startswith("entrada"):
            precio = _f(r.get("Precio USD", ""))
            cant = _f(r.get("Tamaño (cant.)", ""))
            valor = _f(r.get("Tamaño de la posición (valor)", ""))
            if precio and cant and valor:
                ratios.append(valor / (precio * cant))
    ppt = round(statistics.median(ratios), 4) if ratios else None
    return cum, ppt


# ---------------------------------------------------------------------------
# ATR estimado para la cola sin HOLC (SPEC §9.2): ATR de la ÚLTIMA barra —
# marcado en enriched/manifest; la costura --stitch-db lo hace innecesario
# ---------------------------------------------------------------------------

def estimate_tail_atr(trades: list[Trade], bars: dict, offset_min: int) -> list[Trade]:
    """Asigna el ATR de la última barra HOLC a los trades POSTERIORES al
    export (sin cobertura). Devuelve los trades estimados (para marcarlos)."""
    keys = sorted(bars)
    last = keys[-1]
    window = [{"high": bars[k][1], "low": bars[k][2], "close": bars[k][3]}
              for k in keys[-(_ATR_LOOKBACK + 1):]]
    atr = _calc_atr(window, _ATR_PERIOD)
    close = bars[last][3]
    if atr is None or close <= 0:
        return []
    delta = timedelta(minutes=offset_min)
    estimated: list[Trade] = []
    for t in trades:
        if t.atr_entry is None and t.entry_ts + delta > last:
            t.atr_entry = round(atr, 6)
            t.bar_close = close
            t.atr_pct = round(atr / close * 100.0, 6)
            t.aligned_ts = t.entry_ts + delta
            t.hour = t.aligned_ts.hour
            estimated.append(t)
    return estimated


# ---------------------------------------------------------------------------
# enriched.csv (SPEC §3): por-trade +ATR14, MAE/MFE×ATR, lado, duración, sesión
# ---------------------------------------------------------------------------

def sesion_et(ts: datetime) -> str:
    """Sesión por hora ET (informativa — el filtro de sesión está descartado)."""
    hm = ts.hour * 60 + ts.minute
    if 9 * 60 + 30 <= hm < 16 * 60:
        return "RTH"
    if 16 * 60 <= hm < 18 * 60:
        return "tarde"
    if hm >= 18 * 60 or hm < 3 * 60:
        return "asia"
    return "europa"


_ENRICHED_COLS = ("number", "side", "entry_ts", "exit_ts", "duracion_min",
                  "sesion", "entry_price", "exit_price", "pnl_usd", "pnl_pct",
                  "mae_pct", "mfe_pct", "atr_entry", "atr_pct", "mae_atr",
                  "mfe_atr", "hora_et", "atr_estimado")


def write_enriched(path: Path, trades: list[Trade], offset_min: int,
                   estimated: set[int]) -> None:
    delta = timedelta(minutes=offset_min)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(_ENRICHED_COLS)
        for t in trades:
            ts_et = t.entry_ts + delta
            dur = (round((t.exit_ts - t.entry_ts).total_seconds() / 60)
                   if t.exit_ts else None)
            w.writerow([
                t.number, t.side, t.entry_ts.isoformat(),
                t.exit_ts.isoformat() if t.exit_ts else "",
                dur if dur is not None else "", sesion_et(ts_et),
                t.entry_price, t.exit_price if t.exit_price is not None else "",
                t.pnl_usd, t.pnl_pct, t.mae_pct, t.mfe_pct,
                t.atr_entry if t.atr_entry is not None else "",
                t.atr_pct if t.atr_pct is not None else "",
                round(t.mae_atr, 4) if t.mae_atr is not None else "",
                round(t.mfe_atr, 4) if t.mfe_atr is not None else "",
                ts_et.hour, int(t.number in estimated),
            ])


# ---------------------------------------------------------------------------
# Manifest reforzado (Directiva 3.5): hash master, última barra HOLC +
# stitch, versión de rejillas, commit git del motor — `recrear` bit a bit
# ---------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def grids_fingerprint() -> str:
    """Huella de las rejillas del núcleo (lab_metrics es la fuente única)."""
    blob = json.dumps({"sl": SL_GRID, "tp": TP_GRID, "pb": PULLBACK_LEVELS})
    return "lab-" + hashlib.sha256(blob.encode()).hexdigest()[:8]


def _git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


# ---------------------------------------------------------------------------
# Carga compartida (integrar y calcular): HOLC + costura + TZ + ATR
# ---------------------------------------------------------------------------

async def _enriquecer(trades: list[Trade], activo: str, stitch: bool,
                      ) -> tuple[dict, int, dict, int, list[Trade]]:
    """(bars, offset, tz_detail, sin_cobertura, estimados) — HOLC + costura
    opcional, validación TZ BLOQUEANTE, ATR(14) vivo y ATR estimado de cola.
    Todo del núcleo del Lab."""
    bars = load_holc(activo, "5m")
    if stitch:
        bars = await stitch_from_db(bars, activo, "5m")
    off, sanity, tz_detail = detect_tz_offset(trades, bars)
    print(f"· TZ offset {off:+d} min · sanity {sanity*100:.0f}% · "
          f"HOLC hasta {max(bars)}{' (+costura DB)' if stitch else ''}")
    if sanity < _MIN_SANITY:
        raise SystemExit(
            f"⛔ BLOQUEADO: sanity TZ {sanity*100:.0f}% < "
            f"{_MIN_SANITY*100:.0f}% — no se puede confiar la alineación.")
    uncovered = enrich_with_bars(trades, bars, off)
    estimated = estimate_tail_atr(trades, bars, off)
    sin_cobertura = uncovered - len(estimated)
    if estimated:
        print(f"⚠ {len(estimated)} trade(s) posteriores al HOLC → ATR "
              f"ESTIMADO (última barra {estimated[0].atr_entry:.2f})."
              + ("" if stitch else " Con --stitch-db la cola se cose y el "
                                   "caveat desaparece."))
    if sin_cobertura:
        print(f"⚠ {sin_cobertura} trade(s) sin cobertura de barras "
              f"(inicio del HOLC): sin ATR.")
    return bars, off, tz_detail, sin_cobertura, estimated


# ---------------------------------------------------------------------------
# integrar
# ---------------------------------------------------------------------------

def _fecha_export(csv_path: Path, override: str | None) -> str:
    if override:
        return override
    m = _FECHA_EN_NOMBRE.search(csv_path.name)
    return m.group(1) if m else date.today().isoformat()


def _superset_faltantes(old_master: Path, new_trades: list[Trade]) -> list[str]:
    """Claves del master anterior AUSENTES en el export nuevo (SPEC §9.1 —
    clave estable (entry_ts, side, entry_price); advertir, no bloquear)."""
    old = parse_luxalgo_csv(old_master)
    new_keys = {(t.entry_ts.isoformat(), t.side, t.entry_price)
                for t in new_trades}
    return sorted(
        f"{t.entry_ts.isoformat()} {t.side} @{t.entry_price}"
        for t in old
        if (t.entry_ts.isoformat(), t.side, t.entry_price) not in new_keys)


async def integrar(csv_path: Path, codigo: str, activo: str | None = None,
                   stitch: bool = False, fecha: str | None = None) -> dict:
    if not csv_path.exists():
        raise SystemExit(f"⛔ No existe el export: {csv_path}")
    activo = activo or csv_instrument(str(csv_path))
    if not activo:
        raise SystemExit("⛔ No pude deducir el instrumento del nombre del "
                         "CSV — pásalo con --activo.")
    fecha = _fecha_export(csv_path, fecha)
    base_dir = MOTOR_DIR / f"{activo}_{codigo}"

    # 1) Parseo (núcleo del Lab) + cuadre AL DÓLAR contra el propio export
    trades = parse_luxalgo_csv(csv_path)
    if not trades:
        raise SystemExit("⛔ CSV sin trades parseables.")
    pnl_parseado = round(sum(t.pnl_usd for t in trades), 2)
    pnl_export, ppt_inferido = read_export_footer(csv_path)
    cuadre_ok = (pnl_export is not None
                 and abs(pnl_parseado - pnl_export) <= 0.01)
    if not cuadre_ok:
        raise SystemExit(
            f"⛔ CUADRE FALLIDO: Σ PnL parseado ${pnl_parseado:,.2f} ≠ "
            f"PyG acumuladas final del export "
            f"${pnl_export if pnl_export is not None else float('nan'):,.2f}. "
            f"No se integra nada.")
    print(f"· {len(trades)} trades · cuadre al dólar ✅ "
          f"(${pnl_parseado:,.2f} == export)")

    # $/punto: inferido del export, verificado contra la tabla conocida
    ppt_conocido = USD_PER_POINT_KNOWN.get(activo)
    ppt = ppt_conocido if ppt_conocido is not None else ppt_inferido
    ppt_ok = None
    if ppt_conocido is not None and ppt_inferido is not None:
        ppt_ok = abs(ppt_inferido - ppt_conocido) / ppt_conocido <= 0.01
        if not ppt_ok:
            print(f"⚠ $/punto inferido {ppt_inferido} ≠ conocido "
                  f"{ppt_conocido} — revisar contrato (¿micro vs mini?)")

    # 2) Superconjunto vs master anterior (advertir, no bloquear — SPEC §9.1)
    master = base_dir / "master.csv"
    faltantes: list[str] = []
    if master.exists():
        faltantes = _superset_faltantes(master, trades)
        if faltantes:
            print(f"⚠ El export nuevo NO es superconjunto del master anterior: "
                  f"{len(faltantes)} trade(s) del master no aparecen "
                  f"(¿export parcial? ¿ventana deslizante de LuxAlgo?):")
            for k in faltantes[:5]:
                print(f"    - {k}")

    # 3-4) HOLC + costura + TZ bloqueante + ATR (núcleo del Lab, compartido
    # con `calcular`)
    bars, off, tz_detail, sin_cobertura, estimated = await _enriquecer(
        trades, activo, stitch)
    stitched = stitch
    holc_last = max(bars)
    estimated_ids = {t.number for t in estimated}

    # 5) Persistencia: snapshot inmutable + master + enriched + manifest
    base_dir.mkdir(parents=True, exist_ok=True)
    (base_dir / "snapshots").mkdir(exist_ok=True)
    (base_dir / "runs").mkdir(exist_ok=True)
    export_hash = _sha256(csv_path)
    snapshot = base_dir / "snapshots" / f"export_{fecha}.csv"
    if snapshot.exists() and _sha256(snapshot) != export_hash:
        snapshot = base_dir / "snapshots" / f"export_{fecha}_{export_hash[:6]}.csv"
    if not snapshot.exists():
        shutil.copyfile(csv_path, snapshot)
    shutil.copyfile(csv_path, master)
    write_enriched(base_dir / "enriched.csv", trades, off, estimated_ids)

    base = metrics_usd([t.pnl_usd for t in trades])
    manifest = {
        "version": MANIFEST_VERSION,
        "activo": activo,
        "codigo": codigo,
        "integrado": fecha,
        "export": {
            "archivo": csv_path.name,
            "sha256_master": export_hash,
            "snapshot": snapshot.name,
        },
        "trades": {
            "n": len(trades),
            "desde": trades[0].entry_ts.isoformat(),
            "hasta": trades[-1].entry_ts.isoformat(),
            "superconjunto_ok": not faltantes,
            "faltantes_vs_anterior": len(faltantes),
        },
        "usd_por_punto": {"config": ppt_conocido, "inferido": ppt_inferido,
                          "usado": ppt, "ok": ppt_ok},
        "holc": {
            "archivo": f"{activo}_5m.csv",
            "ultima_barra": holc_last.isoformat(),
            "stitch_db": stitched,
            "sin_cobertura": sin_cobertura,
            "atr_estimado": len(estimated),
        },
        "tz": tz_detail,
        "grids_version": grids_fingerprint(),
        "motor_commit": _git_commit(),
        "linea_base_usd": base,
        "cuadre": {"pnl_parseado": pnl_parseado, "pnl_export": pnl_export,
                   "ok": cuadre_ok},
        "ultima_corrida": None,      # la escribe `calcular` (MR-2+)
    }
    (base_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")

    print(f"\n── Línea base ({activo}_{codigo} · USD · 1 contrato) ──")
    print(f"  Total PnL      ${base['net_usd']:>12,.2f}    "
          f"Trades {base['n']}  (rentables {base['ganadores']} · "
          f"WR {base['wr_pct']}%)")
    print(f"  Profit Factor  {base['pf']:>13}    "
          f"Bruta +${base['ganancia_bruta_usd']:,.2f} / "
          f"−${base['perdida_bruta_usd']:,.2f}")
    print(f"  Max Drawdown   ${base['max_dd_usd']:>12,.2f}    "
          f"({base['max_dd_pct_hwm']}% del pico de equity)")
    print(f"  Peor trade     ${base['peor_trade_usd']:>12,.2f}")
    print(f"\n✅ Integrado → {base_dir}")
    return manifest


# ---------------------------------------------------------------------------
# calcular (MR-2): corre los estudios de riesgo y persiste el resultado
# ---------------------------------------------------------------------------

def _fmt_usd(v) -> str:
    return f"${v:,.2f}" if v is not None else "—"


async def calcular(clave: str, stitch: bool = False, oos: float = 0.3,
                   fecha: str | None = None,
                   hc: HaircutCfg | None = None) -> dict:
    """Corre los estudios MR-2 sobre el master de `clave` y escribe
    runs/estudios_<fecha>.json (MR-3 lo convertirá en .md + heatmap +
    recomendación). Determinista: la fecha viene del parámetro (recrear)."""
    hc = hc or HaircutCfg()
    base_dir = MOTOR_DIR / clave
    man_path = base_dir / "manifest.json"
    if not man_path.exists():
        raise SystemExit(f"⛔ No hay listado integrado en {base_dir} — "
                         f"corre `integrar` primero.")
    man = json.loads(man_path.read_text(encoding="utf-8"))
    activo = man["activo"]
    ppt = man["usd_por_punto"]["usado"]
    fecha = fecha or date.today().isoformat()

    trades = parse_luxalgo_csv(base_dir / "master.csv")
    bars, off, tz_detail, sin_cobertura, estimated = await _enriquecer(
        trades, activo, stitch)
    split_in_out(trades, oos)

    # Pullback del Lab (ventana 180 min) para RECONCILIAR los fill-rates de
    # la escalera — misma caminata B4.0 que el visor (solo trades con barras).
    keys5 = sorted(bars)
    idx5 = {k: i for i, k in enumerate(keys5)}
    covered = [t for t in trades if t.aligned_ts in idx5]
    pb = pullback_study(covered, keys5, idx5, bars)
    lab_rates = {lvl: d["fill_rate"] for lvl, d in pb.items()}

    sts = from_trades(trades, ppt, {t.number for t in estimated})
    res = run_studies(sts, ppt, hc, lab_rates)
    res["meta"] = {
        "clave": clave, "activo": activo, "codigo": man["codigo"],
        "fecha": fecha, "oos": oos, "usd_por_punto": ppt,
        "master_sha256": man["export"]["sha256_master"],
        "holc_ultima_barra": max(bars).isoformat(),
        "stitch_db": stitch,
        "n_trades_listado": len(trades),
        "sin_cobertura": sin_cobertura,
        "atr_estimado": len(estimated),
        "grids_version": grids_fingerprint(),
        "motor_commit": _git_commit(),
        "tz": tz_detail,
    }
    out = base_dir / "runs" / f"estudios_{fecha}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(res, indent=1, ensure_ascii=False),
                   encoding="utf-8")

    # MR-3 — entregables de la corrida (md + csv + heatmap + recomendación)
    from scripts.mr_report import generar_entregables
    entregables = generar_entregables(res, base_dir / "runs")

    man["ultima_corrida"] = entregables["md"].name
    man_path.write_text(json.dumps(man, indent=1, ensure_ascii=False),
                        encoding="utf-8")

    _print_resumen_estudios(clave, res)
    print(f"\n✅ Estudios → {out}")
    for tipo, p in entregables.items():
        print(f"   {tipo:14} {p if p else '— (matplotlib no disponible)'}")
    return res


def _print_resumen_estudios(clave: str, res: dict) -> None:
    base = res["linea_base"]["total"]
    print(f"\n════ ESTUDIOS DE RIESGO — {clave} "
          f"(universo {res['universo']['n']} trades, "
          f"{res['universo']['n_atr_estimado']} con ATR estimado) ════")
    print(f"\nLínea base: net {_fmt_usd(base['net_usd'])} · PF {base['pf']} "
          f"· DD {_fmt_usd(base['max_dd_usd'])} · "
          f"peor {_fmt_usd(base['peor_trade_usd'])}")

    g = res["mae_floor"]["ganadoras_mae_atr"]
    print(f"\n▎Suelo del SL (MAE×ATR ganadoras): mediana {g['mediana']} · "
          f"p90 {g['p90']} · p95 {g['p95']} · máx {g['max']}")
    print(f"  {res['mae_floor']['veredicto']}")

    b = res["backstop"]["optimo"]
    if b:
        print(f"\n▎Backstop óptimo: {_fmt_usd(b['backstop_usd'])} = "
              f"{b['backstop_pts']:.0f} pts ≈ {b['x_atr_mediana']}×ATR · "
              f"toca {b['tocados']} · Δnet {_fmt_usd(b['delta_net_usd'])} · "
              f"ΔDD {b['delta_dd_pct']}% · peor c/gap {b['peor_con_gap_usd']}")
    else:
        print("\n▎Backstop: NINGÚN nivel del grid suma vs base (revisar).")

    print("\n▎TP nominal (por ENCIMA del cierre de LuxAlgo — que cierre "
          "LuxAlgo):")
    for lado, d in res["tp"]["por_lado"].items():
        c = d["cierre_atr"]
        print(f"  {lado:5}: cierra p95 {c['p95']}× / p99 {c['p99']}× → "
              f"TP nominal {d['tp_nominal_atr']}×ATR "
              f"(dispararía en {d['tp_nominal_dispararia_pct']}% de los "
              f"trades) · en la mesa {_fmt_usd(d['en_la_mesa_usd'])}")
    tm = res["tp"]["tp_meta_mejor"]
    if tm:
        print(f"  TP-meta INFORMATIVO óptimo: L{tm['tp_long']}/"
              f"S{tm['tp_short']} (net {_fmt_usd(tm['net_usd'])}, "
              f"PF out {tm['pf_out']}) — no es la recomendación")

    ls = res["ls"]
    print(f"\n▎Asimetría L/S — {ls['lectura']}:")
    for lado in ("long", "short"):
        m = ls[lado]
        print(f"  {lado:5}: n {m['n']} · net {_fmt_usd(m['net_usd'])} · "
              f"PF {m['pf']} · WR {m['wr_pct']}% · "
              f"peor {_fmt_usd(m['peor_trade_usd'])} · "
              f"give-backs≥3×ATR {m['giveback_perdedores_3atr']}")

    rec = res.get("reconciliacion_fills")
    if rec:
        print(f"\n▎Reconciliación fills escalera↔pullback Lab: "
              f"Δ máx en niveles someros (≤2×ATR) = "
              f"{rec['max_delta_somero_pp']} pp")

    print("\n▎Configs (top por net; gating = supera base + sobrevive OOS):")
    print(f"  {'config':52} {'net':>10} {'PF':>5} {'DD':>8} {'peor':>8} "
          f"{'part%':>6}  estado")
    aprobadas = [c for c in res["configs"] if c["gate"]["estado"] == "aprobada"]
    alta = [c for c in aprobadas if "alta_participacion" in c["etiquetas"]]
    top = res["configs"][:6]
    # la mejor de alta participación SIEMPRE visible (Directiva 3.1)
    if alta and alta[0] not in top:
        top.append(alta[0])
    for c in top:
        t = c["total"]
        marca = " ⚠n" if c["low_n_out"] else ""
        print(f"  {c['nombre'][:52]:52} {t['net_usd']:>10,.0f} "
              f"{t['pf'] if t['pf'] is not None else '—':>5} "
              f"{t['max_dd_usd']:>8,.0f} {t['peor_trade_usd']:>8,.0f} "
              f"{c['participacion_pct']:>6} "
              f" {c['gate']['estado']}{marca}")
    print(f"  ({len(aprobadas)} aprobadas de {len(res['configs'])} configs; "
          f"descartados por diseño: SL duro ×ATR, sesión, time-stop)")

    rob = res.get("robustez")
    if rob:
        h2h = rob.get("head_to_head")
        if h2h:
            print("\n▎Head-to-head (walk-forward):")
            for rol, t in (("líder net  ", h2h["lider_net"]),
                           ("líder score", h2h["lider_score"])):
                bl = t["bloques"]
                print(f"  {rol}: {t['nombre'][:44]:44} PF OOS "
                      f"{bl['out']['pf']} (Δ{bl['out']['delta_pf']:+}) · "
                      f"H1 {bl['h1']['pf']} / H2 {bl['h2']['pf']} → "
                      f"{t['veredicto']}")
        estres = rob.get("estres_pierna_profunda")
        if estres:
            c = estres["contribucion"]
            print(f"▎Estrés pierna profunda ({estres['micros']} MES @ "
                  f"{estres['depth_atr']}×): {estres['n_fills']} fills "
                  f"({estres['fills_por_bloque']['in']}in/"
                  f"{estres['fills_por_bloque']['out']}out · "
                  f"H1 {estres['fills_por_bloque']['h1']}/"
                  f"H2 {estres['fills_por_bloque']['h2']}) · "
                  f"aporta {c['total_usd']:+,.0f} "
                  f"({c['ganadores']}W/{c['perdedores']}L)")
        if rob["elegido"]:
            el = rob["elegido"]
            wf = el["walk_forward"]["bloques"]["out"]
            print(f"▎ELEGIDO: {el['nombre']} — PF OOS {wf['pf']} "
                  f"(Δ{wf['delta_pf']:+}) · {el['walk_forward']['veredicto']}")
        else:
            print("▎ELEGIDO: ninguno (nada validado por el walk-forward)")


# ---------------------------------------------------------------------------
# estado
# ---------------------------------------------------------------------------

def estado(clave: str | None = None) -> list[dict]:
    manifests = sorted(MOTOR_DIR.glob("*/manifest.json"))
    if clave:
        manifests = [p for p in manifests if p.parent.name == clave]
    if not manifests:
        print("(sin listados integrados — corre `integrar` primero)"
              + (f" [filtro: {clave}]" if clave else ""))
        return []
    out = []
    for p in manifests:
        m = json.loads(p.read_text(encoding="utf-8"))
        t, h = m["trades"], m["holc"]
        cobertura = ("✓" if not (h["atr_estimado"] or h["sin_cobertura"])
                     else f"⚠ {h['atr_estimado']} ATR estimado"
                          + (f", {h['sin_cobertura']} sin ATR"
                             if h["sin_cobertura"] else ""))
        ultima = m.get("ultima_corrida")
        runs = ([p.parent / "runs" / ultima] if ultima
                else sorted(p.parent.glob("runs/*")))
        print(f"▸ {p.parent.name}")
        print(f"    trades      {t['n']}  ({t['desde'][:10]} → "
              f"{t['hasta'][:10]})")
        print(f"    HOLC        hasta {h['ultima_barra'][:16]} "
              f"{'(+costura DB) ' if h['stitch_db'] else ''}· {cobertura}")
        print(f"    línea base  ${m['linea_base_usd']['net_usd']:,.2f} · "
              f"PF {m['linea_base_usd']['pf']} · "
              f"DD ${m['linea_base_usd']['max_dd_usd']:,.2f}")
        print(f"    integrado   {m['integrado']} "
              f"({m['export']['archivo']})")
        print(f"    últ. corrida {runs[-1].name if runs else '—'}")
        out.append(m)
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        prog="nt_riesgo", description="Motor de Riesgo NTEXECG (MR-1)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_int = sub.add_parser("integrar", help="integrar un export de LuxAlgo")
    p_int.add_argument("csv", type=Path)
    p_int.add_argument("--codigo", required=True,
                       help="código ESTABLE de la estrategia (llave de la "
                            "carpeta MotorRiesgo/<ACTIVO>_<codigo>)")
    p_int.add_argument("--activo", default=None,
                       help="instrumento (default: del nombre del CSV)")
    p_int.add_argument("--stitch-db", action="store_true",
                       help="coser la cola HOLC desde Postgres (solo lectura)")
    p_int.add_argument("--fecha", default=None,
                       help="fecha del export (default: del nombre del CSV)")

    p_cal = sub.add_parser("calcular",
                           help="correr los estudios de riesgo (MR-2)")
    p_cal.add_argument("clave", help="carpeta, p.ej. ES_ConfNormal_TC_TSR")
    p_cal.add_argument("--stitch-db", action="store_true",
                       help="coser la cola HOLC desde Postgres (solo lectura)")
    p_cal.add_argument("--oos", type=float, default=0.3,
                       help="fracción out-of-sample (default 0.3, como el Lab)")
    p_cal.add_argument("--fecha", default=None,
                       help="fecha de la corrida (default: hoy; `recrear` "
                            "la pasará explícita)")
    p_cal.add_argument("--comision", type=float, default=0.0,
                       help="haircut: $ por contrato round-turn (default 0 = "
                            "paridad referencia)")
    p_cal.add_argument("--slip-pts", type=float, default=0.0,
                       help="haircut: fricción en pts por pierna llenada")
    p_cal.add_argument("--gap-pts", type=float, default=0.0,
                       help="haircut: deslizamiento del backstop en pts "
                            "(el estrés 0/10/25 se reporta siempre)")

    p_est = sub.add_parser("estado", help="resumen de los listados integrados")
    p_est.add_argument("clave", nargs="?", default=None,
                       help="carpeta específica, p.ej. ES_ConfNormal_TC_TSR")

    sub.add_parser("recrear", help="(MR-4 — aún no implementado)")

    args = ap.parse_args()
    if args.cmd == "integrar":
        asyncio.run(integrar(args.csv, args.codigo, args.activo,
                             args.stitch_db, args.fecha))
    elif args.cmd == "calcular":
        hc = HaircutCfg(comision_rt_usd=args.comision,
                        slip_pts=args.slip_pts, gap_pts=args.gap_pts)
        asyncio.run(calcular(args.clave, args.stitch_db, args.oos,
                             args.fecha, hc))
    elif args.cmd == "estado":
        estado(args.clave)
    else:
        raise SystemExit("`recrear` llega en MR-4 (fases del SPEC §10).")


if __name__ == "__main__":
    main()
