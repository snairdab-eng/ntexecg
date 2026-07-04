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
  estado [<clave>]
      Resumen por carpeta MotorRiesgo/: nº trades, rango, cobertura HOLC,
      última integración, última corrida.

`calcular` / `recrear` llegan en MR-2+ (fases del SPEC §10).

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
from app.services.lab_metrics import PULLBACK_LEVELS, SL_GRID, TP_GRID, aggregate
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
    stitch_from_db,
)
from scripts.lab_manifest import csv_instrument

MOTOR_DIR = Path("MotorRiesgo")
MANIFEST_VERSION = 1

# $/punto conocidos por instrumento (SPEC §6) — se VERIFICAN contra el export
# (`Tamaño de la posición (valor)` / precio·cant); el inferido manda si el
# instrumento no está en la tabla (6E/6J y futuros nuevos).
USD_PER_POINT_KNOWN = {"ES": 50.0, "NQ": 20.0, "RTY": 50.0, "YM": 5.0,
                       "GC": 100.0, "CL": 1000.0}

_FECHA_EN_NOMBRE = re.compile(r"_(\d{4}-\d{2}-\d{2})_")


# ---------------------------------------------------------------------------
# Métricas de línea base en USD (reusa lab_metrics.aggregate, que es
# unit-agnóstico: entra USD → sale USD; aquí solo se renombra y se añade lo
# que el núcleo no trae — brutas y DD% sobre high-water mark, SPEC §6)
# ---------------------------------------------------------------------------

def metrics_usd(pnls: list[float]) -> dict:
    m = aggregate(pnls)          # claves *_pct, valores en las unidades de entrada
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

    # 3) HOLC + costura opcional + validación TZ BLOQUEANTE (núcleo del Lab)
    bars = load_holc(activo, "5m")
    stitched = False
    if stitch:
        bars = await stitch_from_db(bars, activo, "5m")
        stitched = True
    holc_last = max(bars)
    off, sanity, tz_detail = detect_tz_offset(trades, bars)
    print(f"· TZ offset {off:+d} min · sanity {sanity*100:.0f}% · "
          f"HOLC hasta {holc_last}{' (+costura DB)' if stitched else ''}")
    if sanity < _MIN_SANITY:
        raise SystemExit(
            f"⛔ BLOQUEADO: sanity TZ {sanity*100:.0f}% < "
            f"{_MIN_SANITY*100:.0f}% — no se puede confiar la alineación.")

    # 4) ATR(14) vivo + ATR estimado para la cola (marcado — SPEC §9.2)
    uncovered = enrich_with_bars(trades, bars, off)
    estimated = estimate_tail_atr(trades, bars, off)
    estimated_ids = {t.number for t in estimated}
    sin_cobertura = uncovered - len(estimated)
    if estimated:
        print(f"⚠ {len(estimated)} trade(s) posteriores al HOLC → ATR "
              f"ESTIMADO (última barra {estimated[0].atr_entry:.2f})."
              + ("" if stitched else " Con --stitch-db la cola se cose y el "
                                     "caveat desaparece."))
    if sin_cobertura:
        print(f"⚠ {sin_cobertura} trade(s) sin cobertura de barras "
              f"(inicio del HOLC): sin ATR.")

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
        runs = sorted(p.parent.glob("runs/Riesgo_*.md"))
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

    p_est = sub.add_parser("estado", help="resumen de los listados integrados")
    p_est.add_argument("clave", nargs="?", default=None,
                       help="carpeta específica, p.ej. ES_confluencia")

    for nombre in ("calcular", "recrear"):
        sub.add_parser(nombre, help="(MR-2+ — aún no implementado)")

    args = ap.parse_args()
    if args.cmd == "integrar":
        asyncio.run(integrar(args.csv, args.codigo, args.activo,
                             args.stitch_db, args.fecha))
    elif args.cmd == "estado":
        estado(args.clave)
    else:
        raise SystemExit(f"`{args.cmd}` llega en MR-2+ (fases del SPEC §10); "
                         f"MR-1 = integrar/estado.")


if __name__ == "__main__":
    main()
