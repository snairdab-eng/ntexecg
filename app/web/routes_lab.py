"""Laboratorio (camino B) — VISOR read-only de la analítica del camino A.

Candados de arquitectura (PROMPT_Laboratorio_CaminoB):
  1. READ-ONLY y sin recompute pesado en request: consume la caché
     `REPORTES/lab_features_<SYM>.json` que genera `scripts/lab_analyze.py`;
     si falta o está vieja → banner con el comando para regenerar.
  2. NO aplica nada a producción (aplicar sigue en los CLI auditados).
  3. Una sola fuente de verdad: la agregación es `app.services.lab_metrics`,
     las MISMAS funciones que usa el reporte offline — paridad exacta.
"""
from __future__ import annotations

import asyncio
import glob
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db

from app.services.lab_metrics import (
    LEG_SHAPES,
    LOW_N_OUT,
    PULLBACK_LEVELS,
    SL_GRID,
    TP_GRID,
    baseline_from_rows,
    combined_config,
    default_config_study,
    deltas_vs_base,
    equity_curve,
    hourly_from_rows,
    lift_from_rows,
    nominal_brackets,
    oos_survivors_from_rows,
    resim_rows,
    tradeoff_read,
    verdict,
)

# Buckets horarios con n < 10 se marcan como poco poblados (igual que el §3
# del reporte offline); la guarda dura anti-espejismo del out sigue en 15.
LOW_N_BUCKET = 10
from app.web.common import render
from app.web.manifest_store import guardar_manifest, load_manifest, lock_integrar

router = APIRouter()

# Patchables en tests (rutas relativas al working dir del servicio, como los CLI)
LAB_DIR = Path("REPORTES")
TRADES_DIR = Path("ListaDeOperaciones")

INSTRUMENTS = ["ES", "NQ", "RTY", "GC", "CL", "6E", "6J", "YM"]
REGEN_CMD = "python -m scripts.lab_analyze --all-summary [--stitch-db]"

# B6.1 — llaves seguras para nombres de cache/estrategia (anti-traversal)
_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# B6.2 — jobs de recálculo en segundo plano: {key: {status, task, tail, ...}}
JOBS: dict[str, dict] = {}


# LAB-1 — `load_manifest`/`guardar_manifest` viven en app.web.manifest_store
# (fuente única compartida con Riesgo). Se re-exportan como símbolos de este
# módulo por retrocompat (los tests y routes_riesgo los usan desde aquí).


def resolve_key(strategy: str | None, instrument: str | None) -> str | None:
    """Llave efectiva del estudio: strategy_id del manifest (B6.1) o
    instrumento (retrocompat). None = inválida (incluye traversal)."""
    key = strategy or instrument or "ES"
    if not _KEY_RE.match(key):
        return None
    if strategy is not None:
        return key if key in load_manifest() else None
    return key if key in INSTRUMENTS or key in load_manifest() else None


def _cache_path(key: str) -> Path:
    return LAB_DIR / f"lab_features_{key}.json"


def delete_lab_cache(key: str) -> bool:
    """Borra la caché del Lab de una estrategia. True si existía. El Lab es el
    dueño del archivo lab_features_<key>.json; Riesgo (v2-D) llama a esto para
    no dejarla huérfana al eliminar la estrategia. Nunca lanza por ausencia."""
    p = _cache_path(key)
    if p.exists():
        p.unlink(missing_ok=True)
        return True
    return False


def rename_lab_cache(old_key: str, new_key: str) -> bool:
    """Mueve la caché del Lab al renombrar la estrategia (v2-D). True si movió.
    Sin caché de origen → no-op. No pisa un destino existente."""
    src = _cache_path(old_key)
    if not src.exists():
        return False
    dst = _cache_path(new_key)
    if dst.exists():
        return False
    src.rename(dst)
    return True


def _csv_mtime(key: str) -> float | None:
    entry = load_manifest().get(key)
    if entry:                                   # B6.1: el CSV de la estrategia
        p = Path(entry["csv"])
        if not p.is_absolute():
            p = TRADES_DIR.parent / p
        return p.stat().st_mtime if p.exists() else None
    hits = sorted(glob.glob(str(TRADES_DIR / f"*_{key}1!_*.csv")))
    return Path(hits[-1]).stat().st_mtime if hits else None


def load_cache(key: str) -> tuple[list[dict], dict] | None:
    """(rows, meta) desde la caché del camino A, o None si no existe.
    `key` = strategy_id (B6.1) o instrumento (retrocompat).

    Acepta el formato con meta ({"meta","rows"}) y el legado (lista pelada).
    meta.stale = el CSV de trades es MÁS NUEVO que la caché (regenerar).
    """
    p = _cache_path(key)
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        rows, meta = data.get("rows") or [], dict(data.get("meta") or {})
    else:                                   # formato legado: lista pelada
        rows, meta = data, {}
    cache_mtime = p.stat().st_mtime
    csv_mtime = _csv_mtime(key)
    entry_ts = [r.get("entry_ts") for r in rows if r.get("entry_ts")]
    meta.setdefault("instrument", key)
    meta.update({
        "key": key,
        "cache_file": p.name,
        "cache_mtime": datetime.fromtimestamp(cache_mtime).isoformat(
            timespec="seconds"),
        "stale": bool(csv_mtime and csv_mtime > cache_mtime),
        "n_trades": len(rows),
        "n_in": sum(1 for r in rows if r.get("in_sample")),
        "n_out": sum(1 for r in rows if not r.get("in_sample")),
        "coverage": ([min(entry_ts), max(entry_ts)] if entry_ts else None),
        "regen_cmd": REGEN_CMD,
        "low_n_threshold": LOW_N_OUT,
    })
    return rows, meta


def _enriched_exists(key: str, instrument: str) -> bool:
    """¿El motor generó el enriched.csv de esta estrategia? (para ofrecer la
    descarga). Import diferido de routes_riesgo (evita el ciclo)."""
    try:
        import app.web.routes_riesgo as rr
        clave = rr.clave_de(key, instrument)
        return (rr.MOTOR_DIR / clave / "enriched.csv").exists()
    except Exception:
        return False


def _datos_ctx(manifest: dict) -> list[dict]:
    """B6.2 — estado de datos por estrategia: CSV actual + fechas + job."""
    out = []
    for key, e in sorted(manifest.items(),
                         key=lambda kv: (kv[1]["instrument"], kv[0])):
        csv_p = Path(e["csv"])
        if not csv_p.is_absolute():
            csv_p = TRADES_DIR.parent / csv_p
        cache_p = _cache_path(key)
        csv_name = Path(e["csv"]).name
        out.append({
            "key": key, "instrument": e["instrument"],
            "csv": csv_name,
            # LAB-2 — un export ORIGINAL (no upload_*) nunca se borra desde la UI.
            "is_upload": csv_name.startswith("upload_"),
            "has_csv": csv_p.exists(),
            "enriched": _enriched_exists(key, e["instrument"]),
            "csv_date": (datetime.fromtimestamp(csv_p.stat().st_mtime)
                         .isoformat(timespec="seconds")
                         if csv_p.exists() else None),
            "cache_date": (datetime.fromtimestamp(cache_p.stat().st_mtime)
                           .isoformat(timespec="seconds")
                           if cache_p.exists() else None),
            "confirmed": bool(e.get("confirmed")),
            "job": (JOBS.get(key) or {}).get("status"),
        })
    return out


async def _live_context(db: AsyncSession, manifest: dict,
                        key: str) -> tuple[list[str], str | None, dict | None]:
    """LAB-2 §4-bis — topología §E, SOLO LECTURA: qué estrategias del manifest
    tienen ficha viva en Estrategias (para el link '⚙ config viva →') y los
    filtros/régimen VIVOS de la actual (pipeline_config_json). El Lab JAMÁS
    escribe a Estrategias — esto es informativo."""
    from app.models.strategy import Strategy
    from app.models.strategy_profile import StrategyProfile
    from app.services.quality_scorer import active_filter_names

    vivas = set((await db.execute(select(Strategy.strategy_id))).scalars().all())
    vivas_manifest = [k for k in manifest if k in vivas]
    if key not in vivas:
        return vivas_manifest, None, None
    prow = (await db.execute(select(StrategyProfile).where(
        StrategyProfile.strategy_id == key))).scalar_one_or_none()
    pcfg = (prow.pipeline_config_json or {}) if prow else {}
    regime = pcfg.get("regime") or {}
    live_config = {
        "filters": active_filter_names(pcfg),
        "regime_enabled": bool(regime.get("enabled")),
        "regime_tf": regime.get("timeframe") or "1h",
        "regime_allowed": (regime.get("allowed_regimes")
                           or regime.get("allowed") or []),
    }
    return vivas_manifest, f"/ui/strategies/{key}", live_config


@router.get("/ui/lab", response_class=HTMLResponse)
async def lab_page(request: Request, instrument: str = "ES",
                   strategy: str | None = None,
                   db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    key = resolve_key(strategy, instrument) or "ES"
    manifest = load_manifest()
    # LAB-2 §4 — el Lab NO da altas: una sola puerta de identidad (el Puente).
    # Si pidieron una estrategia que no está en el manifest, se avisa y el CTA
    # manda a Riesgo.
    unknown_strategy = strategy if (strategy and strategy not in manifest) else None
    # B6.1 — selector por ESTRATEGIA agrupada por símbolo (con manifest);
    # sin manifest, los 8 instrumentos (retrocompat).
    groups: dict[str, list[str]] = {}
    for k, e in sorted(manifest.items(),
                       key=lambda kv: (kv[1]["instrument"], kv[0])):
        groups.setdefault(e["instrument"], []).append(k)
    cached = load_cache(key)
    datos = _datos_ctx(manifest) if manifest else None
    # LAB-1 — ficha del dato de ESTA llave (identidad siempre visible, como el
    # "MASTER EN USO" del motor): CSV del manifest + fechas. None si la llave es
    # un instrumento sin manifest (retrocompat).
    ficha = next((d for d in (datos or []) if d["key"] == key), None)
    # §4-bis — links a config viva + comparación informativa (read-only).
    vivas_manifest, live_link, live_config = await _live_context(db, manifest, key)
    ctx: dict = {"instruments": INSTRUMENTS, "instrument": instrument,
                 "key": key, "groups": groups,
                 "datos": datos, "ficha": ficha,
                 "export_name": (ficha["csv"] if ficha else None),
                 "unknown_strategy": unknown_strategy,
                 "vivas_manifest": vivas_manifest,
                 "live_link": live_link, "live_config": live_config,
                 "regen_cmd": REGEN_CMD, "meta": None, "base": None,
                 "hours": None, "low_n_bucket": LOW_N_BUCKET}
    if cached is not None:
        rows, meta = cached
        ctx["meta"] = meta
        # Línea base y edge-por-hora con las MISMAS funciones que el reporte.
        ctx["base"] = baseline_from_rows(rows)
        ctx["hours"] = hourly_from_rows(rows)
        # B3 — pullback agregado offline por el camino A (el visor no camina
        # barras); ordenado por nivel numérico.
        pb = meta.get("pullback") or None
        ctx["pullback"] = (sorted(pb.items(), key=lambda kv: float(kv[0]))
                           if pb else None)
        ctx["sl_grid"] = list(SL_GRID)
        ctx["tp_grid"] = list(TP_GRID)
        # B5.1 — presets de piernas someras (los mismos del estudio default)
        ctx["leg_shapes"] = [{"label": name, "legs": [list(l) for l in legs]}
                             for name, legs in LEG_SHAPES]
        # B5.2 — brackets nominales por estrategia (TP = MFE p99, SL = MAE p99)
        ctx["nominal"] = nominal_brackets(rows)
        # LAB-3 — reconciliación del listado crudo contra el master del Motor
        # (read-only, sin recompute: lee un JSON pequeño). None si no hay master.
        key_instrument = (manifest.get(key) or {}).get("instrument") or (
            key if key in INSTRUMENTS else None)
        ctx["reconciliation"] = (
            _reconciliation_for(key, key_instrument, rows)
            if key_instrument else None)
    return await render(request, "lab.html", ctx)


def _reconciliation_for(key: str, instrument: str,
                        rows: list[dict]) -> dict | None:
    """LAB-3 — estado de la llave frente al master del Motor (mismo espíritu
    que el badge de deriva del Puente). None si no hay filas o no hay master.
    A prueba de todo: cualquier fallo → None (sin línea)."""
    if not rows:
        return None
    try:
        import app.web.routes_riesgo as rr
        from scripts.lab_motor_reconcile import reconcile_one

        motor = rr._motor_manifest(rr.clave_de(key, instrument))
        return reconcile_one(rows, motor) if motor else None
    except Exception:
        return None


@router.get("/ui/lab/data")
async def lab_data(instrument: str = "ES",
                   strategy: str | None = None) -> JSONResponse:
    """Matriz de features cacheada + metadatos (read-only)."""
    key = resolve_key(strategy, instrument)
    if key is None:
        return JSONResponse({"error": "llave inválida"}, status_code=400)
    cached = load_cache(key)
    if cached is None:
        return JSONResponse(
            {"error": "cache_missing", "regen_cmd": REGEN_CMD},
            status_code=409,
        )
    rows, meta = cached
    return JSONResponse({"meta": meta, "rows": rows})


class Selection(BaseModel):
    instrument: str = "ES"
    strategy: str | None = None               # B6.1: llave por estrategia
    subs: dict[str, int] | None = None       # {"volume_relative": 60, ...}
    regime: dict | None = None                # {"tf": "1h", "allowed": [...]}
    ema: list[str] | None = None              # ["1h20", ...]


@router.post("/ui/lab/aggregate")
async def lab_aggregate(sel: Selection) -> JSONResponse:
    """Agrega una selección what-if — llama al núcleo COMPARTIDO del camino A
    (lab_metrics), así el número del UI es idéntico al del reporte offline."""
    key = resolve_key(sel.strategy, sel.instrument)
    if key is None:
        return JSONResponse({"error": "llave inválida"}, status_code=400)
    cached = load_cache(key)
    if cached is None:
        return JSONResponse(
            {"error": "cache_missing", "regen_cmd": REGEN_CMD},
            status_code=409,
        )
    rows, meta = cached
    selection = {"subs": sel.subs or {}, "regime": sel.regime or {},
                 "ema": sel.ema or []}
    base = baseline_from_rows(rows)
    result = lift_from_rows(rows, selection)
    deltas = deltas_vs_base(result, base)
    return JSONResponse({
        "selection": selection,
        "base": base,
        "result": result,
        "deltas": deltas,
        # B4.2 — veredicto visual (heat 1–10 + sobrevive) y frase de tradeoff,
        # computados en el SERVIDOR con el núcleo (nada de métricas en JS).
        "verdict": verdict(result, deltas),
        "tradeoff": {"in": tradeoff_read(deltas["in"]),
                     "out": tradeoff_read(deltas["out"])},
        "low_n_out": result["low_n_out"],
        "meta": {"stale": meta["stale"], "n_trades": meta["n_trades"]},
    })


@router.get("/ui/lab/best")
async def lab_best(instrument: str = "ES",
                   strategy: str | None = None) -> JSONResponse:
    """B4.3 — "mejor configuración (out-of-sample)": corre la búsqueda de
    supervivientes con la MISMA lógica del RESUMEN (lab_metrics: ΔPF > 0
    dentro Y fuera, elegido por ΔPF OUT — nunca por in-sample, ahí vive el
    espejismo). Ganador = mayor ΔPF out; ninguno → nativo domina."""
    key = resolve_key(strategy, instrument)
    if key is None:
        return JSONResponse({"error": "llave inválida"}, status_code=400)
    cached = load_cache(key)
    if cached is None:
        return JSONResponse(
            {"error": "cache_missing", "regen_cmd": REGEN_CMD},
            status_code=409,
        )
    rows, meta = cached
    survivors = oos_survivors_from_rows(rows)
    return JSONResponse({
        "survivors": survivors,
        "winner": survivors[0] if survivors else None,
        "none_robust": not survivors,
        "meta": {"stale": meta["stale"], "n_trades": meta["n_trades"]},
    })


class CombinedSel(BaseModel):
    instrument: str = "ES"
    strategy: str | None = None
    subs: dict[str, int] | None = None
    regime: dict | None = None
    ema: list[str] | None = None
    sl_k: float | None = None
    tp: float | None = None
    legs: list[list[float]] | None = None    # [[depth, weight], ...]


@router.post("/ui/lab/combined")
async def lab_combined(sel: CombinedSel) -> JSONResponse:
    """B5.1 — LA config combinada (un solo estado): sustractivos → SL/TP
    sobre el subconjunto → piernas. Núcleo: lab_metrics.combined_config
    (orden documentado ahí; con una perilla degrada al camino aislado)."""
    key = resolve_key(sel.strategy, sel.instrument)
    if key is None:
        return JSONResponse({"error": "llave inválida"}, status_code=400)
    if sel.sl_k is not None and sel.sl_k not in SL_GRID:
        return JSONResponse(
            {"error": f"sl_k fuera de la grilla {list(SL_GRID)}"},
            status_code=400)
    if sel.tp is not None and sel.tp not in TP_GRID:
        return JSONResponse(
            {"error": f"tp fuera de la grilla {list(TP_GRID)}"},
            status_code=400)
    legs = sel.legs
    if legs:
        depths = [float(d) for d, _w in legs]
        weights = [float(w) for _d, w in legs]
        if any(d > 0 and d not in PULLBACK_LEVELS for d in depths):
            return JSONResponse(
                {"error": f"pierna fuera de la grilla {list(PULLBACK_LEVELS)}"},
                status_code=400)
        if any(w <= 0 for w in weights) or abs(sum(weights) - 1.0) > 0.01:
            return JSONResponse(
                {"error": "los pesos de las piernas deben ser > 0 y sumar 1"},
                status_code=400)
        if sel.sl_k is not None and any(d >= sel.sl_k for d in depths if d > 0):
            return JSONResponse(
                {"error": "pierna a profundidad ≥ SL (no tiene sentido)"},
                status_code=400)

    cached = load_cache(key)
    if cached is None:
        return JSONResponse(
            {"error": "cache_missing", "regen_cmd": REGEN_CMD},
            status_code=409)
    rows, meta = cached

    selection = {"subs": sel.subs or {}, "regime": sel.regime or {},
                 "ema": sel.ema or []}
    base = baseline_from_rows(rows)
    result = combined_config(rows, selection=selection, sl_k=sel.sl_k,
                             tp=sel.tp, legs=legs)
    outcomes = result.pop("outcomes")
    scaling = result.pop("scaling")
    universe = [row for row in rows if row.get("atr_pct") is not None]
    native = [row["pnl_pct"] for row in universe]
    split_idx = sum(1 for row in universe if row["in_sample"])
    deltas = deltas_vs_base(result, base)
    return JSONResponse({
        "base": base,
        "result": {"in": result["in"], "out": result["out"]},
        "deltas": deltas,
        "verdict": verdict(result, deltas),
        "tradeoff": {"in": tradeoff_read(deltas["in"]),
                     "out": tradeoff_read(deltas["out"])},
        "curves": {"base": equity_curve(native),
                   "combined": equity_curve(outcomes),
                   "split_idx": split_idx},
        # LAB-3 — la señal de fills aproximados que la UI muestra vive en
        # `scaling.approx_fills`; el top-level `approx_fills` era un duplicado
        # que nadie leía (quitado). El eco `config` tampoco se renderizaba.
        "scaling": scaling,
        "low_n_out": result["low_n_out"],
        "meta": {"stale": meta["stale"], "n_trades": meta["n_trades"]},
    })


@router.get("/ui/lab/default")
async def lab_default(instrument: str = "ES",
                      strategy: str | None = None) -> JSONResponse:
    """B4.3 — config DEFAULT recomendada por RIESGO (principio rector:
    disminuir el riesgo de LuxAlgo, no maximizar ganancia): SL catastrófico
    anclado + escalonado somero + sizing a riesgo fijo 1%, elegida por
    OUT-of-sample con expectancy OOS > 0 (lab_metrics.default_config_study;
    el visor solo la muestra — aplicar sigue en los CLI auditados)."""
    key = resolve_key(strategy, instrument)
    if key is None:
        return JSONResponse({"error": "llave inválida"}, status_code=400)
    cached = load_cache(key)
    if cached is None:
        return JSONResponse(
            {"error": "cache_missing", "regen_cmd": REGEN_CMD},
            status_code=409,
        )
    rows, meta = cached
    study = default_config_study(rows)
    return JSONResponse({
        **study,
        "meta": {"stale": meta["stale"], "n_trades": meta["n_trades"]},
    })


class ResimSel(BaseModel):
    instrument: str = "ES"
    strategy: str | None = None
    sl_k: float | None = None    # grilla {1.5..8} — como el §2 del reporte
    tp: float | None = None      # grilla del §8/§9 (B5.2: nominales altos)


@router.post("/ui/lab/resim")
async def lab_resim(sel: ResimSel) -> JSONResponse:
    """Cambia-desenlace (SL/TP re-sim) — MISMA función que §2/§8/§9 del
    reporte (lab_metrics.resim_rows); el orden intrabar sale de los toques
    cacheados por el camino A. Devuelve además las curvas de equity."""
    key = resolve_key(sel.strategy, sel.instrument)
    if key is None:
        return JSONResponse({"error": "llave inválida"}, status_code=400)
    if sel.sl_k is None and sel.tp is None:
        return JSONResponse({"error": "elige SL y/o TP"}, status_code=400)
    if sel.sl_k is not None and sel.sl_k not in SL_GRID:
        return JSONResponse(
            {"error": f"sl_k fuera de la grilla {list(SL_GRID)}"},
            status_code=400)
    if sel.tp is not None and sel.tp not in TP_GRID:
        return JSONResponse(
            {"error": f"tp fuera de la grilla {list(TP_GRID)}"},
            status_code=400)

    cached = load_cache(key)
    if cached is None:
        return JSONResponse(
            {"error": "cache_missing", "regen_cmd": REGEN_CMD},
            status_code=409)
    rows, meta = cached

    base = baseline_from_rows(rows)
    r = resim_rows(rows, sl_k=sel.sl_k, tp=sel.tp)
    outcomes = r.pop("outcomes")
    universe = [row for row in rows if row.get("atr_pct") is not None]
    native = [row["pnl_pct"] for row in universe]
    split_idx = sum(1 for row in universe if row["in_sample"])
    # Cache legado (sin toques): el conjunto SL+TP degrada a "ambos → SL"
    # (conservador) — avisar para regenerar.
    legacy = (sel.sl_k is not None and sel.tp is not None
              and not any(row.get("t_sl_touch") for row in universe))
    deltas = deltas_vs_base(r, base)
    return JSONResponse({
        "base": base,
        "result": {"in": r["in"], "out": r["out"]},
        "deltas": deltas,
        "verdict": verdict(r, deltas),
        "tradeoff": {"in": tradeoff_read(deltas["in"]),
                     "out": tradeoff_read(deltas["out"])},
        "low_n_out": r["low_n_out"],
        "curves": {"base": equity_curve(native),
                   "resim": equity_curve(outcomes),
                   "split_idx": split_idx},
        "legacy_cache": legacy,
        "meta": {"stale": meta["stale"], "n_trades": meta["n_trades"]},
    })


# ---------------------------------------------------------------------------
# B6.2 — gestión de datos: subir CSV + recalcular en SEGUNDO PLANO.
# Único punto de escritura del visor (CSV + manifest + cachés vía el script);
# NO toca dispatch, config de producción ni TradersPost.
# ---------------------------------------------------------------------------

@router.post("/ui/lab/upload")
async def lab_upload(strategy: str = Form(...),
                     file: UploadFile = File(...),
                     recalc: bool = Form(True)) -> JSONResponse:
    """Sube un CSV de LuxAlgo etiquetado con su strategy_id. Se VALIDA con el
    parser real ANTES de aceptar (basura no entra); actualiza el manifest y
    ENCADENA el recálculo de la caché (LAB-1, opt-out con recalc=false: un
    upload deja la pestaña fresca sin un segundo clic). Nunca recomputa en el
    request — el recalc corre en subproceso con polling (mismo mecanismo JOBS;
    si ya hay uno de esta llave, no lo pisa)."""
    manifest = load_manifest()
    if not _KEY_RE.match(strategy or "") or strategy not in manifest:
        return JSONResponse({"error": "estrategia fuera del manifest"},
                            status_code=400)
    raw = await file.read()
    if len(raw) > 20_000_000:
        return JSONResponse({"error": "archivo demasiado grande"},
                            status_code=400)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = TRADES_DIR / f"upload_{strategy}_{ts}.csv"
    TRADES_DIR.mkdir(exist_ok=True)
    dest.write_bytes(raw)
    from scripts.lab_analyze import parse_luxalgo_csv
    try:
        trades = parse_luxalgo_csv(dest)
    except Exception:
        trades = []
    if not trades:
        dest.unlink(missing_ok=True)
        return JSONResponse(
            {"error": "el CSV no parsea como ListaDeOperaciones de LuxAlgo"},
            status_code=400)
    rel = dest.relative_to(TRADES_DIR.parent).as_posix()
    # SERIALIZADO por estrategia (fuente única + lock compartidos): dos subidas
    # simultáneas de la misma clave no se pisan el manifest.
    async with lock_integrar(strategy):
        manifest = load_manifest()                    # releer bajo el lock
        manifest[strategy] = {**(manifest.get(strategy) or {}), "csv": rel}
        guardar_manifest(manifest)
    # Encadenar recalc (opt-out). El job corre en 2º plano; polling en la UI.
    enqueued = _enqueue_recalc(strategy) if recalc else False
    return JSONResponse({
        "ok": True, "csv": rel, "n_trades": len(trades),
        "recalc": ("running" if enqueued
                   else ("busy" if recalc else "skipped")),
        "hint": ("recálculo encolado — la caché se regenera en 2º plano"
                 if enqueued else
                 "ya hay un recálculo corriendo para esta estrategia"
                 if recalc else
                 "recalcula para regenerar la caché"),
    })


def _recalc_cmd(key: str, is_strategy: bool) -> list[str]:
    """Comando del job (separado para tests). En el server, LAB_RECALC_STITCH=1
    añade --stitch-db (la cola de Postgres) y HOLC_DIR viene del env."""
    import os
    cmd = [sys.executable, "-m", "scripts.lab_analyze"]
    cmd += ["--strategy", key] if is_strategy else ["--instrument", key]
    if (os.environ.get("LAB_RECALC_STITCH") or "").lower() in ("1", "true"):
        cmd.append("--stitch-db")
    return cmd


async def _run_recalc(key: str, cmd: list[str]) -> None:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT)
        out, _ = await proc.communicate()
        tail = (out or b"").decode("utf-8", errors="replace")[-2000:]
        JOBS[key].update({
            "status": "done" if proc.returncode == 0 else "error",
            "rc": proc.returncode, "tail": tail,
            "finished": datetime.now().isoformat(timespec="seconds"),
        })
    except Exception as exc:                      # el job NUNCA muere mudo
        JOBS[key].update({"status": "error", "tail": repr(exc)})


def _enqueue_recalc(key: str) -> bool:
    """Encola el job de recalc de `key` en 2º plano (mismo mecanismo JOBS).
    Devuelve False si ya hay uno corriendo (no lo pisa). Punto único usado
    por el endpoint /ui/lab/recalc y por el encadenado del upload (LAB-1)."""
    if (JOBS.get(key) or {}).get("status") == "running":
        return False
    cmd = _recalc_cmd(key, is_strategy=key in load_manifest())
    JOBS[key] = {"status": "running",
                 "started": datetime.now().isoformat(timespec="seconds"),
                 "tail": ""}
    JOBS[key]["task"] = asyncio.create_task(_run_recalc(key, cmd))
    return True


class RecalcReq(BaseModel):
    instrument: str = "ES"
    strategy: str | None = None


@router.post("/ui/lab/recalc", status_code=202)
async def lab_recalc(req: RecalcReq) -> JSONResponse:
    """Dispara la regeneración de la caché en SEGUNDO PLANO (subproceso del
    camino A — nada de recomputo pesado en el hilo de la petición)."""
    key = resolve_key(req.strategy, req.instrument)
    if key is None:
        return JSONResponse({"error": "llave inválida"}, status_code=400)
    if not _enqueue_recalc(key):
        return JSONResponse({"error": "ya hay un recálculo corriendo"},
                            status_code=409)
    return JSONResponse({"ok": True, "status": "running", "key": key},
                        status_code=202)


@router.get("/ui/lab/recalc/status")
async def lab_recalc_status(instrument: str = "ES",
                            strategy: str | None = None) -> JSONResponse:
    key = resolve_key(strategy, instrument)
    if key is None:
        return JSONResponse({"error": "llave inválida"}, status_code=400)
    job = JOBS.get(key)
    if job is None:
        return JSONResponse({"status": "idle"})
    return JSONResponse({k: v for k, v in job.items() if k != "task"})


# ---------------------------------------------------------------------------
# LAB-2 — compartir/descargar el listado y eliminar los datos (espejo v2-D).
# Read-only sobre los artefactos; eliminar jamás toca la estrategia viva de la
# DB ni un export ORIGINAL del operador (solo los upload_* que subió la pestaña).
# ---------------------------------------------------------------------------

def _manifest_csv_path(entry: dict) -> Path:
    """Path ABSOLUTO del CSV vigente del manifest (relativo a la raíz del repo,
    la carpeta padre de ListaDeOperaciones)."""
    p = Path(entry.get("csv") or "")
    return p if p.is_absolute() else (TRADES_DIR.parent / p)


def _inside(path: Path, base: Path) -> Path | None:
    """Anti-traversal ESTRICTO: resuelve `path` y exige que quede DENTRO de
    `base`. None si escapa (symlink/.. incluidos) — nunca sirve fuera del árbol."""
    try:
        rp = path.resolve()
        if rp.is_relative_to(base.resolve()):
            return rp
    except (OSError, ValueError):
        pass
    return None


@router.get("/ui/lab/csv")
async def lab_csv(strategy: str, kind: str = "listado"):
    """Descarga el CSV vigente del manifest (kind=listado) o el enriched del
    motor (kind=enriched). Anti-traversal estricto: el path resuelto debe
    quedar dentro de ListaDeOperaciones/ (o MotorRiesgo/). READ-ONLY."""
    key = resolve_key(strategy, None)          # exige estar en el manifest
    if key is None:
        return JSONResponse({"error": "estrategia fuera del manifest "
                                      "(o llave inválida)"}, status_code=400)
    entry = load_manifest().get(key) or {}

    if kind == "enriched":
        import app.web.routes_riesgo as rr
        clave = rr.clave_de(key, entry.get("instrument") or key)
        rp = _inside(rr.MOTOR_DIR / clave / "enriched.csv", rr.MOTOR_DIR)
        if rp is None or not rp.exists():
            return JSONResponse(
                {"error": "sin enriched — corre Calcular en la pestaña Riesgo"},
                status_code=404)
        return FileResponse(rp, media_type="text/csv",
                            filename=f"{key}_enriched.csv")

    rp = _inside(_manifest_csv_path(entry), TRADES_DIR)
    if rp is None:
        return JSONResponse({"error": "ruta de CSV fuera de "
                                      "ListaDeOperaciones (rechazado)"},
                            status_code=400)
    if not rp.exists():
        return JSONResponse({"error": "sin CSV para esta estrategia "
                                      "(subí uno o revisá el manifest)"},
                            status_code=404)
    fecha = datetime.fromtimestamp(rp.stat().st_mtime).strftime("%Y-%m-%d")
    return FileResponse(rp, media_type="text/csv",
                        filename=f"{key}_{fecha}.csv")


@router.delete("/ui/lab/datos")
async def lab_datos_eliminar(strategy: str) -> JSONResponse:
    """Elimina los DATOS del Lab: la caché lab_features_<key>.json y el CSV
    SOLO si es un upload_* (jamás un export original del operador — regla
    v2-D). La entrada del manifest se CONSERVA (queda "sin datos", lista para
    subir otro). No toca el motor ni la estrategia viva."""
    key = resolve_key(strategy, None)
    if key is None:
        return JSONResponse({"error": "estrategia fuera del manifest"},
                            status_code=400)
    if (JOBS.get(key) or {}).get("status") == "running":
        return JSONResponse({"error": "hay un recálculo corriendo — "
                                      "espera a que termine"}, status_code=409)
    async with lock_integrar(key):
        entry = load_manifest().get(key) or {}
        cache_borrada = delete_lab_cache(key)
        csv_p = Path(entry.get("csv") or "")
        abs_csv = _manifest_csv_path(entry)
        csv_borrado = csv_p.name.startswith("upload_") and abs_csv.exists()
        if csv_borrado:
            abs_csv.unlink(missing_ok=True)
        JOBS.pop(key, None)
    return JSONResponse({"ok": True, "strategy": key,
                         "cache_borrada": cache_borrada,
                         "csv_borrado": csv_borrado,
                         "nota": "el manifest conserva la estrategia — "
                                 "subí un CSV nuevo para regenerar"})
