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

import glob
import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

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

router = APIRouter()

# Patchables en tests (rutas relativas al working dir del servicio, como los CLI)
LAB_DIR = Path("REPORTES")
TRADES_DIR = Path("ListaDeOperaciones")

INSTRUMENTS = ["ES", "NQ", "RTY", "GC", "CL", "6E", "6J", "YM"]
REGEN_CMD = "python -m scripts.lab_analyze --all-summary [--stitch-db]"


def _cache_path(instrument: str) -> Path:
    return LAB_DIR / f"lab_features_{instrument}.json"


def _csv_mtime(instrument: str) -> float | None:
    hits = sorted(glob.glob(str(TRADES_DIR / f"*_{instrument}1!_*.csv")))
    return Path(hits[-1]).stat().st_mtime if hits else None


def load_cache(instrument: str) -> tuple[list[dict], dict] | None:
    """(rows, meta) desde la caché del camino A, o None si no existe.

    Acepta el formato con meta ({"meta","rows"}) y el legado (lista pelada).
    meta.stale = el CSV de trades es MÁS NUEVO que la caché (regenerar).
    """
    p = _cache_path(instrument)
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        rows, meta = data.get("rows") or [], dict(data.get("meta") or {})
    else:                                   # formato legado: lista pelada
        rows, meta = data, {}
    cache_mtime = p.stat().st_mtime
    csv_mtime = _csv_mtime(instrument)
    entry_ts = [r.get("entry_ts") for r in rows if r.get("entry_ts")]
    meta.update({
        "instrument": instrument,
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


@router.get("/ui/lab", response_class=HTMLResponse)
async def lab_page(request: Request, instrument: str = "ES") -> HTMLResponse:
    if instrument not in INSTRUMENTS:
        instrument = "ES"
    cached = load_cache(instrument)
    ctx: dict = {"instruments": INSTRUMENTS, "instrument": instrument,
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
    return await render(request, "lab.html", ctx)


@router.get("/ui/lab/data")
async def lab_data(instrument: str = "ES") -> JSONResponse:
    """Matriz de features cacheada + metadatos (read-only)."""
    if instrument not in INSTRUMENTS:
        return JSONResponse({"error": "instrumento inválido"}, status_code=400)
    cached = load_cache(instrument)
    if cached is None:
        return JSONResponse(
            {"error": "cache_missing", "regen_cmd": REGEN_CMD},
            status_code=409,
        )
    rows, meta = cached
    return JSONResponse({"meta": meta, "rows": rows})


class Selection(BaseModel):
    instrument: str = "ES"
    subs: dict[str, int] | None = None       # {"volume_relative": 60, ...}
    regime: dict | None = None                # {"tf": "1h", "allowed": [...]}
    ema: list[str] | None = None              # ["1h20", ...]


@router.post("/ui/lab/aggregate")
async def lab_aggregate(sel: Selection) -> JSONResponse:
    """Agrega una selección what-if — llama al núcleo COMPARTIDO del camino A
    (lab_metrics), así el número del UI es idéntico al del reporte offline."""
    if sel.instrument not in INSTRUMENTS:
        return JSONResponse({"error": "instrumento inválido"}, status_code=400)
    cached = load_cache(sel.instrument)
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
        "instrument": sel.instrument,
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
async def lab_best(instrument: str = "ES") -> JSONResponse:
    """B4.3 — "mejor configuración (out-of-sample)": corre la búsqueda de
    supervivientes con la MISMA lógica del RESUMEN (lab_metrics: ΔPF > 0
    dentro Y fuera, elegido por ΔPF OUT — nunca por in-sample, ahí vive el
    espejismo). Ganador = mayor ΔPF out; ninguno → nativo domina."""
    if instrument not in INSTRUMENTS:
        return JSONResponse({"error": "instrumento inválido"}, status_code=400)
    cached = load_cache(instrument)
    if cached is None:
        return JSONResponse(
            {"error": "cache_missing", "regen_cmd": REGEN_CMD},
            status_code=409,
        )
    rows, meta = cached
    survivors = oos_survivors_from_rows(rows)
    return JSONResponse({
        "instrument": instrument,
        "survivors": survivors,
        "winner": survivors[0] if survivors else None,
        "none_robust": not survivors,
        "meta": {"stale": meta["stale"], "n_trades": meta["n_trades"]},
    })


class CombinedSel(BaseModel):
    instrument: str = "ES"
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
    if sel.instrument not in INSTRUMENTS:
        return JSONResponse({"error": "instrumento inválido"}, status_code=400)
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

    cached = load_cache(sel.instrument)
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
        "instrument": sel.instrument,
        "config": {"selection": selection, "sl_k": sel.sl_k, "tp": sel.tp,
                   "legs": legs},
        "base": base,
        "result": {"in": result["in"], "out": result["out"]},
        "deltas": deltas,
        "verdict": verdict(result, deltas),
        "tradeoff": {"in": tradeoff_read(deltas["in"]),
                     "out": tradeoff_read(deltas["out"])},
        "curves": {"base": equity_curve(native),
                   "combined": equity_curve(outcomes),
                   "split_idx": split_idx},
        "scaling": scaling,
        "approx_fills": result["approx_fills"],
        "low_n_out": result["low_n_out"],
        "meta": {"stale": meta["stale"], "n_trades": meta["n_trades"]},
    })


@router.get("/ui/lab/default")
async def lab_default(instrument: str = "ES") -> JSONResponse:
    """B4.3 — config DEFAULT recomendada por RIESGO (principio rector:
    disminuir el riesgo de LuxAlgo, no maximizar ganancia): SL catastrófico
    anclado + escalonado somero + sizing a riesgo fijo 1%, elegida por
    OUT-of-sample con expectancy OOS > 0 (lab_metrics.default_config_study;
    el visor solo la muestra — aplicar sigue en los CLI auditados)."""
    if instrument not in INSTRUMENTS:
        return JSONResponse({"error": "instrumento inválido"}, status_code=400)
    cached = load_cache(instrument)
    if cached is None:
        return JSONResponse(
            {"error": "cache_missing", "regen_cmd": REGEN_CMD},
            status_code=409,
        )
    rows, meta = cached
    study = default_config_study(rows)
    return JSONResponse({
        "instrument": instrument,
        **study,
        "meta": {"stale": meta["stale"], "n_trades": meta["n_trades"]},
    })


class ResimSel(BaseModel):
    instrument: str = "ES"
    sl_k: float | None = None    # grilla {1.5..8} — como el §2 del reporte
    tp: float | None = None      # grilla {3,4,6} — como el §8/§9


@router.post("/ui/lab/resim")
async def lab_resim(sel: ResimSel) -> JSONResponse:
    """Cambia-desenlace (SL/TP re-sim) — MISMA función que §2/§8/§9 del
    reporte (lab_metrics.resim_rows); el orden intrabar sale de los toques
    cacheados por el camino A. Devuelve además las curvas de equity."""
    if sel.instrument not in INSTRUMENTS:
        return JSONResponse({"error": "instrumento inválido"}, status_code=400)
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

    cached = load_cache(sel.instrument)
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
        "instrument": sel.instrument,
        "sl_k": sel.sl_k, "tp": sel.tp,
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
