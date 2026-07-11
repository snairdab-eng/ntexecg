"""Strategy management UI routes."""
from __future__ import annotations

import csv as _csv
import io
import json
import re
import secrets
import time as _time

from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.asset_profile import AssetProfile
from app.models.decision import StrategyDecision
from app.models.normalized_signal import NormalizedSignal
from app.models.strategy import Strategy
from app.models.strategy_performance import StrategyPerformance
from app.models.strategy_profile import StrategyProfile
from app.models.symbol_map import SymbolMap
from app.services.audit_service import AuditService
from app.core.config import settings as app_settings
from app.core.security import hash_token
from app.web.common import render, redirect, flash_messages, templates


# NX-26 — parsers compartidos de listas "0, 1, 4" (antes duplicados 4 veces
# entre update_scale_entry y update_profiles).
def _parse_floats(raw: str) -> list:
    out = []
    for tok in (raw or "").replace(";", ",").split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            f = float(tok)
        except ValueError:
            continue
        if f > 0:
            out.append(f)
    return out


def _parse_ints(raw: str) -> list:
    out = []
    for tok in (raw or "").replace(";", ",").split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            out.append(max(0, int(float(tok))))
        except ValueError:
            continue
    return out

router = APIRouter()

# ---------------------------------------------------------------------------
# EST-2 — veredicto del Lab por filtro en la ficha (SOLO LECTURA).
#
# Topología VINCULANTE: Lab→Estrategias es informativo; aquí NUNCA se aplica ni
# se escribe nada. Se lee la caché del camino A (REPORTES/lab_features_<key>.json
# vía routes_lab.load_cache) y se re-agrega con las MISMAS funciones de
# lab_metrics que usa el visor/reporte (paridad exacta): selection_mask + aggregate
# sobre el universo con cobertura de barras, in/out-of-sample. El criterio de
# "candidato" es el MISMO de los supervivientes OOS del Lab (ΔPF>0 dentro Y fuera).
#
# Mapeo EXPLÍCITO nombre de filtro de producción (checkbox de la ficha /
# quality_scorer._NAMES) ↔ sub-score del Lab (lab_metrics.selection_mask). Hoy es
# 1:1, pero el mapeo es explícito y testeado: si un nombre divergiera, el veredicto
# seguiría leyendo la evidencia correcta.
FILTER_TO_LAB_SUB = {
    "volume_relative": "volume_relative",
    "atr_normalized": "atr_normalized",
    "vwap_position": "vwap_position",
    "time_of_day": "time_of_day",
}

# Recompute barato pero NO por request: memoizado por (key, mtime de la caché).
# Un mtime nuevo (regeneró el Lab) invalida; el TTL acota memoria/tiempo sin
# acoplar nada a lab_analyze (no se precomputa al regenerar — topología limpia).
_LAB_VERDICT_TTL = 300.0
_LAB_VERDICT_CACHE: dict = {}   # strategy_id -> (cache_mtime, monotonic_ts, verdicts)


def _lab_filter_verdicts(rows: list[dict]) -> dict[str, dict]:
    """Veredicto OOS por filtro de producción, en paridad con el Lab.

    Para cada filtro barre la grilla de umbrales del Lab (lab_metrics.SUB_THRESHOLDS),
    re-agrega in/out con selection_mask + aggregate (net_usd y PF), y clasifica:
      - candidato: existe umbral con ΔPF>0 DENTRO y FUERA (criterio superviviente
        OOS del Lab) — se reporta el de mayor ΔPF out.
      - no_aporta: ningún umbral sobrevive — se reporta el de mayor ΔPF out (el
        "menos malo") para mostrar la evidencia honesta.
      - sin_datos: sin OOS comparable (PF/net None).
    """
    from app.services import lab_metrics as lm

    universe = [r for r in rows if r.get("atr_pct") is not None]
    base_in = [r for r in universe if r["in_sample"]]
    base_out = [r for r in universe if not r["in_sample"]]

    def agg(sel: list[dict]) -> dict:
        return lm.aggregate([r["pnl_pct"] for r in sel],
                            [r.get("pnl_usd") or 0.0 for r in sel])

    b_in, b_out = agg(base_in), agg(base_out)

    def _d(a, b):
        return round(a - b, 2) if a is not None and b is not None else None

    verdicts: dict[str, dict] = {}
    for fname, sub in FILTER_TO_LAB_SUB.items():
        cands: list[dict] = []
        for thr in lm.SUB_THRESHOLDS:
            sel = {"subs": {sub: thr}}
            a_in = agg([r for r in base_in if lm.selection_mask(r, sel)])
            a_out = agg([r for r in base_out if lm.selection_mask(r, sel)])
            d_pf_in, d_pf_out = _d(a_in["pf"], b_in["pf"]), _d(a_out["pf"], b_out["pf"])
            cands.append({
                "thr": thr,
                "d_net_out": _d(a_out["net_usd"], b_out["net_usd"]),
                "pf_base_out": b_out["pf"], "pf_kept_out": a_out["pf"],
                "d_pf_in": d_pf_in, "d_pf_out": d_pf_out, "n_out": a_out["n"],
                "survivor": (d_pf_in is not None and d_pf_out is not None
                             and d_pf_in > 0 and d_pf_out > 0),
            })
        survs = [c for c in cands if c["survivor"]]
        comparables = [c for c in cands if c["d_pf_out"] is not None]
        if survs:
            best, state = max(survs, key=lambda c: c["d_pf_out"]), "candidato"
        elif comparables:
            best, state = max(comparables, key=lambda c: c["d_pf_out"]), "no_aporta"
        else:
            best, state = {}, "sin_datos"
        verdicts[fname] = {
            "state": state,
            "d_net_out": best.get("d_net_out"),
            "pf_base_out": best.get("pf_base_out"),
            "pf_kept_out": best.get("pf_kept_out"),
            "n_out": best.get("n_out", 0),
            "low_n_out": bool(best) and best.get("n_out", 0) < lm.LOW_N_OUT,
        }
    return verdicts


def lab_evidence_for(strategy_id: str) -> dict | None:
    """Bloque de evidencia del Lab para la ficha (o None si la estrategia no
    está en el manifest del Lab). SOLO LECTURA: load_manifest + load_cache;
    ni una escritura. Memoiza el veredicto por (key, mtime de caché)."""
    import app.web.routes_lab as rl

    if strategy_id not in rl.load_manifest():
        return None
    lab_link = f"/ui/lab?strategy={strategy_id}"
    cached = rl.load_cache(strategy_id)
    if cached is None:                      # en el manifest pero sin caché aún
        return {"available": False, "stale": False, "cache_date": None,
                "n_out": 0, "lab_link": lab_link, "verdicts": {}}
    rows, meta = cached
    mtime = meta.get("cache_mtime")
    now = _time.monotonic()
    hit = _LAB_VERDICT_CACHE.get(strategy_id)
    if hit and hit[0] == mtime and now - hit[1] < _LAB_VERDICT_TTL:
        verdicts = hit[2]
    else:
        verdicts = _lab_filter_verdicts(rows)
        _LAB_VERDICT_CACHE[strategy_id] = (mtime, now, verdicts)
    return {"available": True, "stale": bool(meta.get("stale")),
            "cache_date": meta.get("cache_mtime"), "n_out": meta.get("n_out", 0),
            "lab_link": lab_link, "verdicts": verdicts}

_VALID_STATUSES = {
    "candidate", "shadow", "paper", "micro", "limited_live", "live",
    "paused", "quarantined", "retired",
}


async def _assets(db: AsyncSession) -> list[AssetProfile]:
    result = await db.execute(
        select(AssetProfile).where(AssetProfile.active.is_(True)).order_by(AssetProfile.symbol)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

@router.get("/ui/strategies", response_class=HTMLResponse)
async def list_strategies(
    request: Request, db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    result = await db.execute(select(Strategy).order_by(Strategy.created_at.desc()))
    strategies = list(result.scalars().all())

    # signals-today count per strategy
    perf_rows = await db.execute(select(StrategyPerformance))
    perf = {p.strategy_id: p for p in perf_rows.scalars().all()}

    items = []
    for s in strategies:
        p = perf.get(s.strategy_id)
        items.append({
            "strategy_id": s.strategy_id,
            "name": s.name,
            "asset_symbol": s.asset_symbol,
            "timeframe": s.timeframe,
            "status": s.status,
            "enabled": s.enabled,
            "pass_rate": float(p.filter_pass_rate) if p and p.filter_pass_rate else None,
            "total_received": p.total_signals_received if p else 0,
        })

    return await render(
        request, "strategies.html",
        {"strategies": items, "messages": flash_messages(request)}, db=db,
    )


# ---------------------------------------------------------------------------
# New
# ---------------------------------------------------------------------------

@router.get("/ui/strategies/ticker-hint", response_class=HTMLResponse)
async def ticker_hint(
    request: Request, asset_symbol: str = "", db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    """HTMX partial: show the exact pine_script_config for the chosen asset."""
    pine = None
    sm_info = None
    if asset_symbol:
        res = await db.execute(
            select(AssetProfile).where(AssetProfile.symbol == asset_symbol)
        )
        ap = res.scalar_one_or_none()
        pine = ap.pine_script_config if ap else None
        # Instrument catalog (Anexo 08 #4) — reference data for the operator.
        smres = await db.execute(
            select(SymbolMap).where(SymbolMap.tv_symbol == asset_symbol)
        )
        sm = smres.scalar_one_or_none()
        if sm is not None and sm.tick_value is not None:
            sm_info = {
                "tick_value": float(sm.tick_value),
                "tick_size": float(sm.tick_size) if sm.tick_size is not None else None,
                "contract_type": sm.contract_type,
            }
    return templates.TemplateResponse(
        request, "partials/ticker_hint.html",
        {"pine": pine, "asset_symbol": asset_symbol, "sm_info": sm_info},
    )


@router.get("/ui/strategies/new", response_class=HTMLResponse)
async def new_strategy_form(
    request: Request, db: AsyncSession = Depends(get_db),
    from_estudio: str | None = None,
) -> HTMLResponse:
    # P3-1: templates_list eliminado — el form lo ignoraba (0 refs en
    # strategy_form.html, auditoría Fase A) y la UI de Templates se retiró.
    #
    # Puente P3 — PROMOCIÓN estudio→viva: llegar con ?from_estudio=<id>
    # prellena el alta desde el estudio validado (id bloqueado — una sola
    # tecleada de identidad en todo el ciclo de vida). El alta nace paper +
    # dry_run como siempre; nada se arma aquí.
    prefill = None
    if from_estudio:
        try:
            import re as _re

            import app.web.routes_riesgo as rr
            from scripts.lab_manifest import MICRO_TO_LAB
            entry = rr.routes_lab.load_manifest().get(from_estudio)
            if entry:
                instrument = (entry["instrument"] or "").upper()
                # micro sugerido: el primero del catálogo que mapea a este
                # instrumento y no ES el instrumento (MES para ES, etc.)
                micro = next((m for m, lab in MICRO_TO_LAB.items()
                              if lab == instrument and m != instrument),
                             instrument)
                m_tf = _re.match(r"^[A-Za-z0-9]*?(\d+m|\d+h)_", from_estudio)
                prefill = {
                    "strategy_id": from_estudio,
                    "name": from_estudio.replace("_", " "),
                    "asset_symbol": micro,
                    "timeframe": m_tf.group(1) if m_tf else "5m",
                }
        except Exception:
            prefill = None
    return await render(
        request, "strategy_form.html",
        {"assets": await _assets(db), "prefill": prefill,
         "from_estudio": from_estudio if prefill else None}, db=db,
    )


@router.post("/ui/strategies/new")
async def create_strategy_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    strategy_id: str = Form(...),
    name: str = Form(...),
    asset_symbol: str = Form(""),
    timeframe: str = Form("5m"),
    sl_atr_multiplier: str = Form(""),
    score_minimum: str = Form(""),
    traderspost_webhook_url: str = Form(""),
    initial_mode: str = Form("paper"),
    enforce_symbol_match: str = Form(""),
    enforce_timeframe_match: str = Form(""),
    signal_max_age_entry_seconds: str = Form(""),
    signal_max_age_exit_seconds: str = Form(""),
    from_estudio: str = Form(""),
) -> RedirectResponse:
    # Reject duplicate strategy_id
    existing = await db.execute(
        select(Strategy).where(Strategy.strategy_id == strategy_id)
    )
    if existing.scalar_one_or_none() is not None:
        return redirect(
            "/ui/strategies/new",
            flash=f"strategy_id '{strategy_id}' ya existe", category="error",
        )

    # NX-22 — el token en claro se muestra UNA vez (flash); la DB guarda hash.
    new_token = secrets.token_urlsafe(24)
    strategy = Strategy(
        strategy_id=strategy_id,
        name=name,
        asset_symbol=asset_symbol or None,
        timeframe=timeframe or None,
        status="candidate",
        enabled=False,
        traderspost_webhook_url=traderspost_webhook_url or None,
        webhook_token=None,
        webhook_token_hash=hash_token(new_token, app_settings.WEBHOOK_TOKEN_SALT),
    )
    db.add(strategy)

    # Strategy profile with overrides
    profile = StrategyProfile(
        strategy_id=strategy_id,
        mode=initial_mode if initial_mode in ("paper", "micro", "limited_live", "live") else "paper",
        traderspost_webhook_url=traderspost_webhook_url or None,
    )
    if sl_atr_multiplier:
        try:
            profile.sl_atr_multiplier = float(sl_atr_multiplier)
        except ValueError:
            pass

    # Anexo 08 #2 — per-strategy guardrails stored in pipeline_config_json.
    guardrails: dict = {}
    if enforce_symbol_match:
        guardrails["enforce_symbol_match"] = True
    if enforce_timeframe_match:
        guardrails["enforce_timeframe_match"] = True
    for _field, _key in (
        (signal_max_age_entry_seconds, "signal_max_age_entry_seconds"),
        (signal_max_age_exit_seconds, "signal_max_age_exit_seconds"),
    ):
        if _field.strip():
            try:
                guardrails[_key] = int(_field)
            except ValueError:
                pass

    # Full registration ficha (machote). Extra fields read from the raw form to
    # avoid an enormous handler signature.
    form = await request.form()

    def _s(key: str) -> str | None:
        v = (form.get(key) or "").strip()
        return v or None

    def _num(key, cast):
        v = (form.get(key) or "").strip()
        if not v:
            return None
        try:
            return cast(v)
        except (ValueError, TypeError):
            return None

    # Identity extras + definition/backtest → Strategy.notes / luxalgo_metrics_json
    strategy.notes = _s("descripcion")
    metrics: dict = {}
    for _k in ("responsable", "toolkit", "trigger", "filter_1", "filter_2",
               "exit_condition", "frequency", "order_size"):
        _v = _s(_k)
        if _v:
            metrics[_k] = _v
    bt: dict = {}
    for _k in ("bt_start", "bt_end"):
        _v = _s(_k)
        if _v:
            bt[_k] = _v
    for _k, _cast in (("num_trades", int), ("winrate", float),
                      ("profit_factor", float), ("net_profit", float),
                      ("max_drawdown", float)):
        _v = _num(_k, _cast)
        if _v is not None:
            bt[_k] = _v
    if bt:
        metrics["backtest"] = bt
    if metrics:
        strategy.luxalgo_metrics_json = metrics

    # Profile pipeline_config_json: guardrails + reference-only sections.
    cfg: dict = {}
    if guardrails:
        cfg["guardrails"] = guardrails
    # NX-12 — score_minimum del form de alta (antes se recibía y se descartaba).
    if score_minimum.strip():
        try:
            _smin = int(float(score_minimum))
            if 1 <= _smin <= 100:
                cfg["score_minimum"] = _smin
        except (ValueError, TypeError):
            pass
    risk_ref: dict = {}
    if form.get("stop_required"):
        risk_ref["stop_required"] = True
    for _k, _cast in (("stop_ticks", int), ("risk_usd_max_operation", float),
                      ("max_contracts", int)):
        _v = _num(_k, _cast)
        if _v is not None:
            risk_ref[_k] = _v
    if risk_ref:
        cfg["risk_reference"] = risk_ref  # documentation only; NOT enforced
    _dedup = _num("dedup_seconds", int)
    if _dedup is not None:
        cfg["dedup_seconds"] = _dedup
    _conf = _s("confirmaciones")
    if _conf:
        cfg["confirmaciones"] = _conf
    routing: dict = {}
    if _s("target_account"):
        routing["target_account"] = _s("target_account")
    if _s("routing_notes"):
        routing["notes"] = _s("routing_notes")
    if routing:
        cfg["routing"] = routing
    if cfg:
        profile.pipeline_config_json = cfg

    # Section 4 scalars: exits-always + forced EOD close.
    if form.get("allow_exits_outside_window"):
        profile.allow_exits_outside_window = True
    _eod = _s("force_flat_time")
    if _eod:
        from datetime import time as _time
        try:
            _hh, _mm = _eod.split(":")[:2]
            profile.force_flat_time = _time(int(_hh), int(_mm))
        except (ValueError, IndexError):
            pass

    db.add(profile)

    await AuditService().log(
        db, actor="admin", action="CREATE", object_type="Strategy",
        object_id=strategy_id, new_value={"name": name, "asset": asset_symbol},
        reason="created via UI",
    )
    await db.commit()
    # Puente P3 — promoción desde el estudio: encadenar directo al diff de
    # aplicar en Riesgo (?aplicar=1 abre el modal). El flash con el token
    # viaja por query param y Riesgo YA renderiza messages — no se pierde.
    if from_estudio.strip() and from_estudio.strip() == strategy_id:
        return redirect(
            f"/ui/riesgo?strategy={strategy_id}&aplicar=1",
            flash=f"Estrategia '{strategy_id}' creada en CANDIDATE — token "
                  f"webhook (cópialo YA, no se volverá a mostrar): "
                  f"{new_token}",
        )
    return redirect(
        f"/ui/strategies/{strategy_id}",
        flash=f"Estrategia '{strategy_id}' creada en CANDIDATE — token webhook "
              f"(cópialo YA, no se volverá a mostrar): {new_token}",
    )


# ---------------------------------------------------------------------------
# L1 — Datos DENTRO de Estrategias: subir la lista → integrar el master (reusa
# el núcleo del Motor, `routes_riesgo.integrar_lista`; NO se muda el motor) +
# provisión de HOLC (subir/validar/guardar) con anti-traversal estricto.
# ---------------------------------------------------------------------------

# Timeframes válidos para el nombre destino del HOLC (whitelist anti-traversal).
_HOLC_TF_OK = {"5m", "15m", "1h", "4h"}
_SYM_RE = re.compile(r"^[A-Z0-9]{1,6}$")


def _lab_instrument(asset_symbol: str | None) -> str | None:
    """Instrumento raíz del Lab/Motor desde el activo de la estrategia
    (Symbol Mapper del estudio: MES→ES). None si no hay activo."""
    from scripts.lab_manifest import MICRO_TO_LAB
    a = (asset_symbol or "").strip().upper()
    if not a:
        return None
    return MICRO_TO_LAB.get(a, a)


def _validate_holc(raw: bytes) -> tuple[bool, int, str | None]:
    """Valida en MEMORIA (sin tocar disco) que el CSV es un HOLC parseable:
    columnas DateTime/Open/High/Low/Close + filas con DateTime YYYY-MM-DD
    HH:MM:SS y OHLC numéricos. Devuelve (ok, filas_muestreadas, error)."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False, 0, "el archivo no es texto UTF-8"
    reader = _csv.DictReader(io.StringIO(text))
    cols = set(reader.fieldnames or [])
    required = {"DateTime", "Open", "High", "Low", "Close"}
    if not required.issubset(cols):
        faltan = sorted(required - cols)
        return False, 0, (f"faltan columnas {faltan} — se esperan "
                          f"DateTime/Open/High/Low/Close")
    n = 0
    for r in reader:
        try:
            datetime.strptime((r["DateTime"] or "").strip(),
                              "%Y-%m-%d %H:%M:%S")
            float(r["Open"]); float(r["High"]); float(r["Low"]); float(r["Close"])
            n += 1
        except (KeyError, ValueError, TypeError):
            continue
        if n >= 20:                     # suficiente para confirmar el formato
            break
    if n < 5:
        return False, n, ("sin filas OHLC parseables (DateTime "
                          "YYYY-MM-DD HH:MM:SS + Open/High/Low/Close numéricos)")
    return True, n, None


@router.post("/ui/strategies/holc")
async def upload_holc(
    symbol: str = Form(...), timeframe: str = Form("5m"),
    file: UploadFile = File(...),
) -> JSONResponse:
    """Provisión de HOLC: valida el formato y lo guarda como <SYM>_<tf>.csv en
    NINJATRADER/HOLC (fuente única `lab_analyze._holc_dir`). SYM se normaliza a
    la raíz del catálogo (MES→ES) y se valida contra whitelist + regex; tf de
    una whitelist — imposible salir del directorio. HOLC inválido → rechazo sin
    tocar disco."""
    from scripts.lab_manifest import MICRO_TO_LAB
    from scripts.lab_analyze import _holc_dir

    raw_sym = (symbol or "").strip().upper()
    tf = (timeframe or "").strip().lower()
    if not _SYM_RE.match(raw_sym):
        return JSONResponse({"error": "símbolo inválido (A-Z/0-9, máx 6)"},
                            status_code=400)
    if tf not in _HOLC_TF_OK:
        return JSONResponse(
            {"error": f"timeframe inválido (válidos: {sorted(_HOLC_TF_OK)})"},
            status_code=400)
    sym = MICRO_TO_LAB.get(raw_sym, raw_sym)      # micro → raíz del catálogo
    catalogo = set(MICRO_TO_LAB.values())
    if sym not in catalogo:
        return JSONResponse(
            {"error": f"símbolo {raw_sym} fuera del catálogo del motor "
                      f"({sorted(catalogo)})"}, status_code=400)

    raw = await file.read()
    if len(raw) > 300_000_000:
        return JSONResponse({"error": "archivo demasiado grande"},
                            status_code=400)
    ok, nrows, err = _validate_holc(raw)
    if not ok:
        return JSONResponse({"error": f"HOLC inválido — {err}"},
                            status_code=400)

    holc_dir = _holc_dir().resolve()
    dest = (holc_dir / f"{sym}_{tf}.csv").resolve()
    # Doble candado anti-traversal: el destino DEBE quedar directo bajo el dir.
    if dest.parent != holc_dir:
        return JSONResponse({"error": "ruta de destino inválida"},
                            status_code=400)
    holc_dir.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)
    return JSONResponse({"ok": True, "symbol": sym, "timeframe": tf,
                         "rows_sampled": nrows, "archivo": dest.name})


# L2 — estudio Luxy como JOB (patrón Calcular): subproceso `python -m
# scripts.mr_luxy <clave>` que persiste runs/luxy_<fecha>.json; polling.
import asyncio as _asyncio
import sys as _sys

LUXY_JOBS: dict[str, dict] = {}


def _luxy_latest(clave: str) -> dict | None:
    """Última corrida Luxy persistida (runs/luxy_*.json) — solo lectura."""
    import json as _json
    import app.web.routes_riesgo as rr
    hits = sorted((rr.MOTOR_DIR / clave / "runs").glob("luxy_*.json"))
    if not hits:
        return None
    try:
        return _json.loads(hits[-1].read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


async def _run_luxy(clave: str, cmd: list[str]) -> None:
    import app.web.routes_riesgo as rr
    try:
        rc, tail = await rr._run_motor(cmd)
        LUXY_JOBS[clave].update({"status": "done" if rc == 0 else "error",
                                 "rc": rc, "tail": tail})
    except Exception as exc:                       # el job NUNCA muere mudo
        LUXY_JOBS[clave].update({"status": "error", "tail": repr(exc)})


@router.post("/ui/strategies/{strategy_id}/luxy/calcular", status_code=202)
async def luxy_calcular(
    strategy_id: str, db: AsyncSession = Depends(get_db)
) -> JSONResponse:
    import app.web.routes_riesgo as rr
    srow = (await db.execute(select(Strategy).where(
        Strategy.strategy_id == strategy_id))).scalar_one_or_none()
    if srow is None:
        return JSONResponse({"error": "estrategia no encontrada"},
                            status_code=404)
    instrument = _lab_instrument(srow.asset_symbol)
    if not instrument:
        return JSONResponse({"error": "la estrategia no tiene activo"},
                            status_code=400)
    clave = rr.clave_de(strategy_id, instrument)
    if rr._motor_manifest(clave) is None:
        return JSONResponse({"error": "sin master integrado — sube la lista "
                                      "primero"}, status_code=409)
    if (LUXY_JOBS.get(clave) or {}).get("status") == "running":
        return JSONResponse({"error": "ya hay un estudio Luxy corriendo"},
                            status_code=409)
    LUXY_JOBS[clave] = {"status": "running",
                        "started": datetime.now(timezone.utc).isoformat()}
    cmd = [_sys.executable, "-m", "scripts.mr_luxy", clave]
    LUXY_JOBS[clave]["task"] = _asyncio.create_task(_run_luxy(clave, cmd))
    return JSONResponse({"ok": True, "status": "running", "clave": clave},
                        status_code=202)


@router.get("/ui/strategies/{strategy_id}/luxy/status")
async def luxy_status(
    strategy_id: str, db: AsyncSession = Depends(get_db)
) -> JSONResponse:
    import app.web.routes_riesgo as rr
    srow = (await db.execute(select(Strategy).where(
        Strategy.strategy_id == strategy_id))).scalar_one_or_none()
    if srow is None:
        return JSONResponse({"error": "estrategia no encontrada"},
                            status_code=404)
    instrument = _lab_instrument(srow.asset_symbol)
    clave = rr.clave_de(strategy_id, instrument) if instrument else None
    job = LUXY_JOBS.get(clave) if clave else None
    if job is None:
        return JSONResponse({"status": "idle"})
    return JSONResponse({k: v for k, v in job.items() if k != "task"})


# L3 — RECALCULAR del dashboard: evalúa las palancas movidas con el evaluador
# de L2 (subproceso `mr_luxy --evaluar`), JOB+polling. Read-only hacia
# producción (aplicar llega en L5).
LUXY_EVAL_JOBS: dict[str, dict] = {}


async def _run_luxy_eval(clave: str, cmd: list[str]) -> None:
    import json as _json
    import app.web.routes_riesgo as rr
    try:
        rc, tail = await rr._run_motor(cmd)
        result = None
        for line in (tail or "").splitlines():
            if line.startswith("LUXY_EVAL_JSON "):
                result = _json.loads(line[len("LUXY_EVAL_JSON "):])
        if rc == 0 and result is not None:
            LUXY_EVAL_JOBS[clave].update({"status": "done", "result": result})
        else:
            LUXY_EVAL_JOBS[clave].update({"status": "error",
                                          "tail": (tail or "")[-400:]})
    except Exception as exc:
        LUXY_EVAL_JOBS[clave].update({"status": "error", "tail": repr(exc)})


@router.post("/ui/strategies/{strategy_id}/luxy/evaluar", status_code=202)
async def luxy_evaluar(
    strategy_id: str, request: Request, db: AsyncSession = Depends(get_db)
) -> JSONResponse:
    import json as _json
    import app.web.routes_riesgo as rr
    srow = (await db.execute(select(Strategy).where(
        Strategy.strategy_id == strategy_id))).scalar_one_or_none()
    if srow is None:
        return JSONResponse({"error": "estrategia no encontrada"},
                            status_code=404)
    instrument = _lab_instrument(srow.asset_symbol)
    if not instrument:
        return JSONResponse({"error": "la estrategia no tiene activo"},
                            status_code=400)
    clave = rr.clave_de(strategy_id, instrument)
    if rr._motor_manifest(clave) is None:
        return JSONResponse({"error": "sin master integrado"}, status_code=409)
    try:
        overrides = await request.json()
    except Exception:
        overrides = {}
    if not isinstance(overrides, dict):
        overrides = {}
    LUXY_EVAL_JOBS[clave] = {"status": "running"}
    cmd = [_sys.executable, "-m", "scripts.mr_luxy", clave,
           "--evaluar", _json.dumps(overrides)]
    LUXY_EVAL_JOBS[clave]["task"] = _asyncio.create_task(
        _run_luxy_eval(clave, cmd))
    return JSONResponse({"ok": True, "status": "running"}, status_code=202)


@router.get("/ui/strategies/{strategy_id}/luxy/evaluar/status")
async def luxy_evaluar_status(
    strategy_id: str, db: AsyncSession = Depends(get_db)
) -> JSONResponse:
    import app.web.routes_riesgo as rr
    srow = (await db.execute(select(Strategy).where(
        Strategy.strategy_id == strategy_id))).scalar_one_or_none()
    if srow is None:
        return JSONResponse({"error": "estrategia no encontrada"},
                            status_code=404)
    instrument = _lab_instrument(srow.asset_symbol)
    clave = rr.clave_de(strategy_id, instrument) if instrument else None
    job = LUXY_EVAL_JOBS.get(clave) if clave else None
    if job is None:
        return JSONResponse({"status": "idle"})
    return JSONResponse({k: v for k, v in job.items() if k != "task"})


@router.post("/ui/strategies/{strategy_id}/integrar")
async def integrar_estrategia(
    strategy_id: str, file: UploadFile = File(...),
    degradado: bool = Form(False),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Alta de datos: sube la lista de operaciones de la estrategia e integra su
    master vía el Motor (reusa `routes_riesgo.integrar_lista` — cuadre al dólar,
    sha256, intrabar; el motor NO se muda). El instrumento sale del ACTIVO de la
    estrategia (R-T9: usd_por_punto del master, no del CSV). Sin HOLC del activo
    y sin `degradado`: 409 holc_missing (la UI ofrece subir HOLC o integrar
    degradado)."""
    import app.web.routes_riesgo as rr

    srow = (await db.execute(select(Strategy).where(
        Strategy.strategy_id == strategy_id))).scalar_one_or_none()
    if srow is None:
        return JSONResponse({"error": "estrategia no encontrada"},
                            status_code=404)
    instrument = _lab_instrument(srow.asset_symbol)
    if not instrument:
        return JSONResponse(
            {"error": "la estrategia no tiene activo — edítalo primero"},
            status_code=400)

    if not degradado and not rr.holc_disponible(instrument):
        return JSONResponse(
            {"ok": False, "holc_missing": True, "instrument": instrument,
             "error": f"no hay HOLC de {instrument} en NINJATRADER/HOLC — "
                      f"súbelo (estudio completo) o integra en modo degradado "
                      f"(sin intrabar)"},
            status_code=409)

    raw = await file.read()
    result = await rr.integrar_lista(strategy_id, instrument, raw,
                                     degradado=degradado, recalc_lab=False)
    if not result["ok"]:
        payload = {k: v for k, v in result.items()
                   if k in ("error", "detalle")}
        return JSONResponse(payload, status_code=result["status"])
    result["instrument"] = instrument
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# EST-1 — evidencia en vivo del Nivel 4 (visibilidad; CERO cambios de pipeline)
# ---------------------------------------------------------------------------
from app.services.hmm_service import HMMService
from app.services.quality_scorer import (
    QualityScorer,
    active_filter_names,
    filters_active as _quality_filters_active,
)
from app.services.symbol_mapper import SymbolMapper

# Caché TTL del régimen: {(data_symbol, tf): (detail, monotonic_ts)}. Un render
# no le pega al bridge más de una vez por minuto por activo (presupuesto acotado).
_REGIME_TTL_S = 60
_regime_cache: dict[tuple[str, str], tuple[dict, float]] = {}


def _market_data(request: Request):
    """MarketDataService cableada en app.state (lifespan) o None en tests sin
    inyección — la evidencia degrada a un aviso suave, nunca revienta."""
    return getattr(request.app.state, "market_data", None)


def _hace(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    s = int((datetime.now(timezone.utc) - dt).total_seconds())
    if s < 60:
        return f"hace {s}s"
    if s < 3600:
        return f"hace {s // 60}m"
    if s < 86400:
        return f"hace {s // 3600}h"
    return f"hace {s // 86400}d"


async def _regime_now(request: Request, db: AsyncSession, tv_symbol: str,
                      timeframe: str = "1h") -> dict | None:
    """Régimen 1h ACTUAL del activo (baseline Kaufman ER) con caché TTL 60s.
    None si no hay market data cableada."""
    md = _market_data(request)
    if md is None:
        return None
    data_symbol = await SymbolMapper().resolve_market_data_symbol(db, tv_symbol)
    key = (data_symbol, timeframe)
    now = _time.monotonic()
    hit = _regime_cache.get(key)
    if hit and now - hit[1] < _REGIME_TTL_S:
        detail = hit[0]
    else:
        detail = await HMMService(md).get_regime_detail(data_symbol, timeframe)
        _regime_cache[key] = (detail, now)
    return {**detail, "data_symbol": data_symbol}


# Etiquetas legibles del régimen (mismas del formulario de Régimen).
_REGIME_LABEL = {
    "trending_bull": "tendencia alcista",
    "trending_bear": "tendencia bajista",
    "ranging": "rango / lateral",
    "unknown": "unknown",
}


@router.get("/ui/strategies/{strategy_id}", response_class=HTMLResponse)
async def strategy_detail(
    request: Request, strategy_id: str, db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    result = await db.execute(
        select(Strategy).where(Strategy.strategy_id == strategy_id)
    )
    strategy = result.scalar_one_or_none()
    if strategy is None:
        return redirect("/ui/strategies", flash="Estrategia no encontrada", category="error")

    prof_res = await db.execute(
        select(StrategyProfile).where(StrategyProfile.strategy_id == strategy_id)
    )
    profile = prof_res.scalar_one_or_none()

    perf_res = await db.execute(
        select(StrategyPerformance).where(StrategyPerformance.strategy_id == strategy_id)
    )
    perf = perf_res.scalar_one_or_none()

    dec_res = await db.execute(
        select(StrategyDecision, NormalizedSignal)
        .join(NormalizedSignal, StrategyDecision.normalized_signal_id == NormalizedSignal.id)
        .where(StrategyDecision.strategy_id == strategy_id)
        .order_by(StrategyDecision.created_at.desc())
        .limit(10)
    )
    decisions = [
        {
            "id": d.id, "time": d.created_at, "outcome": d.outcome,
            "ticker": s.ticker_received, "action": s.action,
            "score": d.score, "block_reason": d.block_reason, "block_level": d.block_level,
        }
        for d, s in dec_res.all()
    ]

    cfg_now = (profile.pipeline_config_json or {}) if profile else {}

    # EST-1 — régimen 1h ACTUAL del activo (evidencia de que el toggle mide algo).
    regime_tf = ((cfg_now.get("regime") or {}).get("timeframe")) or "1h"
    try:
        regime_now = await _regime_now(request, db, strategy.asset_symbol, regime_tf)
    except Exception:
        regime_now = None
    if regime_now is not None:
        regime_now["label"] = _REGIME_LABEL.get(regime_now.get("regime"),
                                                regime_now.get("regime"))

    # EST-1 — ÚLTIMA evaluación real que llegó a L4 (score persistido). El
    # desglose por filtro no se guarda (score_breakdown_json no se escribe), así
    # que se muestra score + umbral + calidad + motivo.
    le = (await db.execute(
        select(StrategyDecision)
        .where(StrategyDecision.strategy_id == strategy_id,
               StrategyDecision.score.isnot(None))
        .order_by(StrategyDecision.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    ultima_eval = None
    if le is not None:
        l4 = ((le.pipeline_execution_json or {}).get("level_4") or {})
        snap = le.config_snapshot_json or {}
        umbral = snap.get("score_minimum")
        if umbral is None:
            umbral = cfg_now.get("score_minimum", 70)
        ultima_eval = {
            "score": le.score, "umbral": umbral,
            "quality": l4.get("quality"),
            "filters_active": l4.get("filters_active"),
            "outcome": le.outcome, "block_reason": le.block_reason,
            "hace": _hace(le.created_at),
        }
    filters_active_now = _quality_filters_active(cfg_now)

    # EST-2 — evidencia informativa del Lab por filtro (read-only, a prueba de
    # todo: sin manifest/caché → None → el bloque no aparece).
    try:
        lab_evidence = lab_evidence_for(strategy_id)
    except Exception:
        lab_evidence = None

    base = str(request.base_url).rstrip("/")
    # NX-22: la URL completa solo puede mostrarse con token legacy en claro;
    # con hash el token ya no es recuperable (token_hashed → hint en la UI).
    webhook_url = (
        f"{base}/webhooks/luxalgo/{strategy.strategy_id}?token={strategy.webhook_token}"
        if strategy.webhook_token else None
    )
    token_hashed = bool(strategy.webhook_token_hash) and not strategy.webhook_token

    # Puente P1 — badge de deriva vs el estudio del Motor de Riesgo (si
    # existe). Solo lectura y a prueba de todo: sin estudio → sin badge.
    deriva_riesgo = None
    try:
        import app.web.routes_riesgo as rr
        entry = rr.routes_lab.load_manifest().get(strategy_id)
        if entry:
            res = rr._latest_estudio(
                rr.clave_de(strategy_id, entry["instrument"]))
            reco = (res or {}).get("recomendacion")
            if reco:
                deriva_riesgo = rr.deriva_estudio(
                    (profile.pipeline_config_json or {}) if profile else {},
                    rr._activacion_json(reco), (res or {}).get("_fecha"))
    except Exception:
        deriva_riesgo = None

    # L1 — selector de estrategia (cambiar sin volver a la lista) + estado de
    # datos: instrumento raíz, master integrado (o no) y si hay HOLC del activo.
    all_rows = (await db.execute(
        select(Strategy.strategy_id, Strategy.name)
        .order_by(Strategy.strategy_id))).all()
    all_strategies = [{"strategy_id": r[0], "name": r[1]} for r in all_rows]
    l1_instrument = _lab_instrument(strategy.asset_symbol)
    l1_master = None
    l1_holc_ok = False
    luxy_study_data = None
    if l1_instrument:
        try:
            import app.web.routes_riesgo as rr
            _clave = rr.clave_de(strategy_id, l1_instrument)
            l1_master = rr._motor_manifest(_clave)
            l1_holc_ok = rr.holc_disponible(l1_instrument)
            luxy_study_data = _luxy_latest(_clave)
        except Exception:
            l1_master = None

    return await render(
        request, "strategy_detail.html",
        {
            "strategy": strategy, "profile": profile, "perf": perf,
            "decisions": decisions, "webhook_url": webhook_url,
            "token_hashed": token_hashed,
            "tp_env_enabled": app_settings.TRADERSPOST_ENABLED,
            "deriva_riesgo": deriva_riesgo,
            "regime_now": regime_now,
            "ultima_eval": ultima_eval,
            "filters_active_now": filters_active_now,
            "lab_evidence": lab_evidence,
            "all_strategies": all_strategies,
            "l1_instrument": l1_instrument,
            "l1_master": l1_master,
            "l1_holc_ok": l1_holc_ok,
            "luxy": luxy_study_data,
            "messages": flash_messages(request),
        }, db=db,
    )


@router.get("/ui/strategies/{strategy_id}/probar-filtros")
async def probar_filtros(
    request: Request, strategy_id: str, db: AsyncSession = Depends(get_db),
    f_volume_relative_enabled: bool = False, f_volume_relative_weight: float = 1.0,
    f_atr_normalized_enabled: bool = False, f_atr_normalized_weight: float = 1.0,
    f_vwap_position_enabled: bool = False, f_vwap_position_weight: float = 1.0,
    f_time_of_day_enabled: bool = False, f_time_of_day_weight: float = 1.0,
    score_minimum: int | None = None,
) -> JSONResponse:
    """EST-1 — 'probar ahora' READ-ONLY: corre el QualityScorer con los pesos/
    checks del form SIN guardar, sobre las barras ACTUALES del bridge, con una
    señal sintética (último cierre, ahora, compra). Muestra el score que daría
    la config antes de activarla — NO emite señal, NO escribe, NO toca el
    pipeline. Sin bridge o sin barras → 409 con aviso."""
    strat = (await db.execute(select(Strategy).where(
        Strategy.strategy_id == strategy_id))).scalar_one_or_none()
    if strat is None:
        return JSONResponse({"error": "estrategia no encontrada"},
                            status_code=404)
    md = _market_data(request)
    if md is None:
        return JSONResponse(
            {"error": "sin bridge de datos conectado — no se puede probar ahora"},
            status_code=409)
    data_symbol = await SymbolMapper().resolve_market_data_symbol(
        db, strat.asset_symbol)
    try:
        bars = await md.get_bars(data_symbol, "5m", limit=100)
    except Exception:
        bars = []
    if not bars:
        return JSONResponse(
            {"error": f"sin barras del bridge para {data_symbol} (5m) — "
                      "¿NinjaTrader exportando?"},
            status_code=409)

    cfg = {"filters": {
        "volume_relative": {"enabled": f_volume_relative_enabled,
                            "weight": f_volume_relative_weight},
        "atr_normalized": {"enabled": f_atr_normalized_enabled,
                           "weight": f_atr_normalized_weight},
        "vwap_position": {"enabled": f_vwap_position_enabled,
                          "weight": f_vwap_position_weight},
        "time_of_day": {"enabled": f_time_of_day_enabled,
                        "weight": f_time_of_day_weight},
    }}
    # Señal SINTÉTICA de prueba (no persiste, no despacha): último cierre.
    try:
        last_close = float(bars[-1].get("close"))
    except (TypeError, ValueError, AttributeError):
        last_close = None
    probe = SimpleNamespace(price=last_close, action="buy",
                            signal_ts=datetime.now(timezone.utc))
    breakdown = await QualityScorer().score_breakdown(probe, bars, cfg)

    smin = score_minimum if (score_minimum and 1 <= score_minimum <= 100) else 70
    breakdown["score_minimum"] = smin
    breakdown["passed"] = breakdown["score"] >= smin
    breakdown["data_symbol"] = data_symbol
    breakdown["n_bars"] = len(bars)
    return JSONResponse(breakdown)


@router.post("/ui/strategies/{strategy_id}/dispatch")
async def update_dispatch(
    request: Request,
    strategy_id: str,
    db: AsyncSession = Depends(get_db),
    action: str = Form(...),
    confirm: str = Form(""),
) -> RedirectResponse:
    """Fase 2 — arm/disarm real dispatch for ONE strategy (CONFIRMAR to arm).

    arm    → traderspost_enabled=True, dry_run=False (requires confirm==CONFIRMAR)
    disarm → dry_run=True (safe direction, no confirmation)
    Real send still also requires the global profile and the env kill-switch.
    """
    prof_res = await db.execute(
        select(StrategyProfile).where(StrategyProfile.strategy_id == strategy_id)
    )
    profile = prof_res.scalar_one_or_none()
    if profile is None:
        profile = StrategyProfile(strategy_id=strategy_id)
        db.add(profile)

    if action == "arm":
        if confirm != "CONFIRMAR":
            return redirect(
                f"/ui/strategies/{strategy_id}",
                flash="Escribe CONFIRMAR para armar el envío real", category="error")
        profile.traderspost_enabled = True
        profile.dry_run = False
        msg = "Envío real ARMADO (sujeto al global y al kill-switch del servidor)"
    elif action == "disarm":
        profile.dry_run = True
        msg = "Estrategia de vuelta en DRY_RUN"
    else:
        return redirect(f"/ui/strategies/{strategy_id}",
                        flash="Acción inválida", category="error")

    await AuditService().log(
        db, actor="admin", action="DISPATCH_CHANGE", object_type="StrategyProfile",
        object_id=strategy_id,
        new_value={"action": action, "traderspost_enabled": profile.traderspost_enabled,
                   "dry_run": profile.dry_run},
        reason="dispatch toggled via UI")
    await db.commit()
    return redirect(f"/ui/strategies/{strategy_id}", flash=msg)


@router.post("/ui/strategies/{strategy_id}/regenerate-token")
async def regenerate_token(
    request: Request,
    strategy_id: str,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Anexo 08 — (re)generate the per-strategy webhook token for LuxAlgo."""
    result = await db.execute(
        select(Strategy).where(Strategy.strategy_id == strategy_id)
    )
    strategy = result.scalar_one_or_none()
    if strategy is None:
        return redirect("/ui/strategies", flash="Estrategia no encontrada",
                        category="error")
    # NX-22 — hash-only; el token en claro solo viaja en este flash.
    new_token = secrets.token_urlsafe(24)
    strategy.webhook_token = None
    strategy.webhook_token_hash = hash_token(
        new_token, app_settings.WEBHOOK_TOKEN_SALT
    )
    await AuditService().log(
        db, actor="admin", action="UPDATE", object_type="Strategy",
        object_id=strategy_id, reason="webhook token regenerated via UI (hashed)",
    )
    await db.commit()
    return redirect(
        f"/ui/strategies/{strategy_id}",
        flash="Token regenerado — actualiza la alerta en LuxAlgo con "
              f"?token={new_token} (no se volverá a mostrar)",
    )


@router.post("/ui/strategies/{strategy_id}/guardrails")
async def update_guardrails(
    request: Request,
    strategy_id: str,
    db: AsyncSession = Depends(get_db),
    enforce_symbol_match: str = Form(""),
    enforce_timeframe_match: str = Form(""),
    signal_max_age_entry_seconds: str = Form(""),
    signal_max_age_exit_seconds: str = Form(""),
) -> RedirectResponse:
    """Anexo 08 #2 — edit the per-strategy guardrails on the detail page."""
    prof_res = await db.execute(
        select(StrategyProfile).where(StrategyProfile.strategy_id == strategy_id)
    )
    profile = prof_res.scalar_one_or_none()
    if profile is None:
        profile = StrategyProfile(strategy_id=strategy_id)
        db.add(profile)

    guardrails: dict = {}
    if enforce_symbol_match:
        guardrails["enforce_symbol_match"] = True
    if enforce_timeframe_match:
        guardrails["enforce_timeframe_match"] = True
    for _field, _key in (
        (signal_max_age_entry_seconds, "signal_max_age_entry_seconds"),
        (signal_max_age_exit_seconds, "signal_max_age_exit_seconds"),
    ):
        if _field.strip():
            try:
                guardrails[_key] = int(_field)
            except ValueError:
                pass

    # Preserve any other pipeline_config_json keys; replace only "guardrails".
    cfg = dict(profile.pipeline_config_json or {})
    if guardrails:
        cfg["guardrails"] = guardrails
    else:
        cfg.pop("guardrails", None)
    profile.pipeline_config_json = cfg or None

    await AuditService().log(
        db, actor="admin", action="UPDATE", object_type="StrategyProfile",
        object_id=strategy_id, new_value={"guardrails": guardrails},
        reason="guardrails updated via UI",
    )
    await db.commit()
    return redirect(
        f"/ui/strategies/{strategy_id}",
        flash="Guardarraíles actualizados",
    )


@router.post("/ui/strategies/{strategy_id}/windows")
async def update_windows(
    request: Request,
    strategy_id: str,
    db: AsyncSession = Depends(get_db),
    windows_json: str = Form(""),
) -> RedirectResponse:
    """Anexo 08 #5 — save repeatable operation windows (days per window)."""
    prof_res = await db.execute(
        select(StrategyProfile).where(StrategyProfile.strategy_id == strategy_id)
    )
    profile = prof_res.scalar_one_or_none()
    if profile is None:
        profile = StrategyProfile(strategy_id=strategy_id)
        db.add(profile)

    try:
        raw = json.loads(windows_json or "[]")
    except (ValueError, TypeError):
        raw = []

    clean: list = []
    if isinstance(raw, list):
        for w in raw:
            if not isinstance(w, dict):
                continue
            start, end = w.get("start"), w.get("end")
            days = w.get("days")
            if not isinstance(days, list) or not start or not end:
                continue
            days_i = sorted({
                int(d) for d in days
                if (isinstance(d, (int, float))
                    or (isinstance(d, str) and d.isdigit()))
                and 0 <= int(d) <= 6
            })
            if not days_i:
                continue
            item: dict = {"days": days_i, "start": str(start), "end": str(end)}
            if w.get("next_day_end"):
                item["next_day_end"] = True
            clean.append(item)

    cfg = dict(profile.pipeline_config_json or {})
    if clean:
        cfg["windows"] = clean
    else:
        cfg.pop("windows", None)
    profile.pipeline_config_json = cfg or None

    await AuditService().log(
        db, actor="admin", action="UPDATE", object_type="StrategyProfile",
        object_id=strategy_id, new_value={"windows": clean},
        reason="windows updated via UI",
    )
    await db.commit()
    return redirect(
        f"/ui/strategies/{strategy_id}",
        flash=f"{len(clean)} ventana(s) guardada(s)",
    )


@router.post("/ui/strategies/{strategy_id}/filters")
async def update_filters(
    request: Request,
    strategy_id: str,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Fase 5 — edit the Level-4 QualityScorer filters (enabled + weight).

    Stored in pipeline_config_json["filters"] as {name: {enabled, weight}}.
    If no filter is enabled (with weight > 0) the key is removed, so the scorer
    returns 100 (pass-through). Weights are preserved while any filter is active.

    NX-12: also persists the per-strategy score_minimum override
    (pipeline_config_json["score_minimum"], 1..100; empty removes the override
    so the global/asset default applies). ConfigResolver already reads it.
    """
    prof_res = await db.execute(
        select(StrategyProfile).where(StrategyProfile.strategy_id == strategy_id)
    )
    profile = prof_res.scalar_one_or_none()
    if profile is None:
        profile = StrategyProfile(strategy_id=strategy_id)
        db.add(profile)

    form = await request.form()
    filters: dict = {}
    any_enabled = False
    for name in ("volume_relative", "atr_normalized", "vwap_position", "time_of_day"):
        enabled = bool(form.get(f"f_{name}_enabled"))
        raw_w = (form.get(f"f_{name}_weight") or "").strip()
        try:
            weight = float(raw_w) if raw_w else 1.0
        except (ValueError, TypeError):
            weight = 1.0
        if weight < 0:
            weight = 0.0
        filters[name] = {"enabled": enabled, "weight": weight}
        if enabled and weight > 0:
            any_enabled = True

    # Merge: replace only the "filters" key, preserving guardrails/windows/etc.
    cfg = dict(profile.pipeline_config_json or {})
    if any_enabled:
        cfg["filters"] = filters
    else:
        cfg.pop("filters", None)

    # NX-12 — per-strategy score_minimum (1..100). Empty → inherit; out of
    # range is discarded (score max is 100 — a 150 would block everything).
    raw_smin = (form.get("score_minimum") or "").strip()
    if raw_smin:
        try:
            _smin = int(float(raw_smin))
        except (ValueError, TypeError):
            _smin = None
        if _smin is not None and 1 <= _smin <= 100:
            cfg["score_minimum"] = _smin
    else:
        cfg.pop("score_minimum", None)
    profile.pipeline_config_json = cfg or None

    await AuditService().log(
        db, actor="admin", action="UPDATE", object_type="StrategyProfile",
        object_id=strategy_id,
        new_value={"filters": filters if any_enabled else {},
                   "score_minimum": cfg.get("score_minimum")},
        reason="quality filters updated via UI",
    )
    await db.commit()
    return redirect(
        f"/ui/strategies/{strategy_id}",
        flash="Filtros de calidad actualizados" if any_enabled
        else "Filtros de calidad desactivados (score 100, pasa-directo)",
    )


@router.post("/ui/strategies/{strategy_id}/regime")
async def update_regime(
    request: Request,
    strategy_id: str,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Fase 6 — edit the Level-4 market-regime gate (opt-in).

    Stored in pipeline_config_json["regime"] as
    {enabled, timeframe, allowed_regimes}. Stored only when enabled AND at
    least one regime is allowed; otherwise the key is removed (gate disabled).
    """
    prof_res = await db.execute(
        select(StrategyProfile).where(StrategyProfile.strategy_id == strategy_id)
    )
    profile = prof_res.scalar_one_or_none()
    if profile is None:
        profile = StrategyProfile(strategy_id=strategy_id)
        db.add(profile)

    form = await request.form()
    enabled = bool(form.get("regime_enabled"))
    timeframe = (form.get("regime_timeframe") or "1h").strip()
    if timeframe not in ("1h", "4h"):
        timeframe = "1h"
    allowed = [
        r for r in ("trending_bull", "trending_bear", "ranging")
        if form.get(f"regime_allow_{r}")
    ]

    cfg = dict(profile.pipeline_config_json or {})
    if enabled and allowed:
        cfg["regime"] = {
            "enabled": True, "timeframe": timeframe, "allowed_regimes": allowed,
        }
    else:
        cfg.pop("regime", None)
    profile.pipeline_config_json = cfg or None

    await AuditService().log(
        db, actor="admin", action="UPDATE", object_type="StrategyProfile",
        object_id=strategy_id, new_value={"regime": cfg.get("regime", {})},
        reason="regime gate updated via UI",
    )
    await db.commit()
    return redirect(
        f"/ui/strategies/{strategy_id}",
        flash="Filtro de régimen actualizado" if (enabled and allowed)
        else "Filtro de régimen desactivado",
    )


@router.post("/ui/strategies/{strategy_id}/ficha")
async def update_ficha(
    request: Request,
    strategy_id: str,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Edit the full registration card (machote) after creation. Mirrors the
    create-form parsing, but MERGES into pipeline_config_json so guardrails /
    windows / filters / regime are preserved.
    """
    result = await db.execute(
        select(Strategy).where(Strategy.strategy_id == strategy_id)
    )
    strategy = result.scalar_one_or_none()
    if strategy is None:
        return redirect("/ui/strategies", flash="Estrategia no encontrada", category="error")
    prof_res = await db.execute(
        select(StrategyProfile).where(StrategyProfile.strategy_id == strategy_id)
    )
    profile = prof_res.scalar_one_or_none()
    if profile is None:
        profile = StrategyProfile(strategy_id=strategy_id, mode="paper")
        db.add(profile)

    form = await request.form()

    def _s(key: str) -> str | None:
        v = (form.get(key) or "").strip()
        return v or None

    def _num(key, cast):
        v = (form.get(key) or "").strip()
        if not v:
            return None
        try:
            return cast(v)
        except (ValueError, TypeError):
            return None

    # Identity / definition / backtest → Strategy.notes + luxalgo_metrics_json
    strategy.notes = _s("descripcion")
    metrics: dict = {}
    for _k in ("responsable", "toolkit", "trigger", "filter_1", "filter_2",
               "exit_condition", "frequency", "order_size"):
        _v = _s(_k)
        if _v:
            metrics[_k] = _v
    bt: dict = {}
    for _k in ("bt_start", "bt_end"):
        _v = _s(_k)
        if _v:
            bt[_k] = _v
    for _k, _cast in (("num_trades", int), ("winrate", float),
                      ("profit_factor", float), ("net_profit", float),
                      ("max_drawdown", float)):
        _v = _num(_k, _cast)
        if _v is not None:
            bt[_k] = _v
    if bt:
        metrics["backtest"] = bt
    strategy.luxalgo_metrics_json = metrics or None

    # Reference/dedup/confirm/routing → MERGE into pipeline_config_json
    cfg = dict(profile.pipeline_config_json or {})
    risk_ref: dict = {}
    if form.get("stop_required"):
        risk_ref["stop_required"] = True
    for _k, _cast in (("stop_ticks", int), ("risk_usd_max_operation", float),
                      ("max_contracts", int)):
        _v = _num(_k, _cast)
        if _v is not None:
            risk_ref[_k] = _v
    if risk_ref:
        cfg["risk_reference"] = risk_ref
    else:
        cfg.pop("risk_reference", None)
    _d = _num("dedup_seconds", int)
    if _d is not None:
        cfg["dedup_seconds"] = _d
    else:
        cfg.pop("dedup_seconds", None)
    # NX-17 — cancel_after / caducidad de la entrada (= entry_reserve_timeout_
    # seconds, la misma clave que libera la reserva de symbol_busy en NX-28).
    # Recordatorio operativo: fijar el MISMO valor en TradersPost.
    _ca = _num("entry_reserve_timeout_seconds", int)
    if _ca is not None and _ca > 0:
        cfg["entry_reserve_timeout_seconds"] = _ca
    else:
        cfg.pop("entry_reserve_timeout_seconds", None)
    _conf = _s("confirmaciones")
    if _conf:
        cfg["confirmaciones"] = _conf
    else:
        cfg.pop("confirmaciones", None)
    routing: dict = {}
    if _s("target_account"):
        routing["target_account"] = _s("target_account")
    if _s("routing_notes"):
        routing["notes"] = _s("routing_notes")
    if routing:
        cfg["routing"] = routing
    else:
        cfg.pop("routing", None)
    profile.pipeline_config_json = cfg or None

    # EOD / exits-always scalars
    profile.allow_exits_outside_window = True if form.get("allow_exits_outside_window") else None
    # NX-13 — tri-estado del cierre EOD: "sin EOD" explícito (checkbox) gana;
    # HH:MM fija la hora; vacío = heredar (global/columna).
    if form.get("force_flat_off"):
        cfg["force_flat_off"] = True
        profile.force_flat_time = None
        profile.pipeline_config_json = cfg or None
    else:
        cfg.pop("force_flat_off", None)
        profile.pipeline_config_json = cfg or None
        _eod = _s("force_flat_time")
        if _eod:
            from datetime import time as _time
            try:
                _hh, _mm = _eod.split(":")[:2]
                profile.force_flat_time = _time(int(_hh), int(_mm))
            except (ValueError, IndexError):
                pass
        else:
            profile.force_flat_time = None

    await AuditService().log(
        db, actor="admin", action="UPDATE", object_type="Strategy",
        object_id=strategy_id, new_value={"ficha": "updated"},
        reason="registration card edited via UI",
    )
    await db.commit()
    return redirect(f"/ui/strategies/{strategy_id}", flash="Ficha actualizada")


@router.post("/ui/strategies/{strategy_id}/edit")
async def update_strategy(
    request: Request,
    strategy_id: str,
    db: AsyncSession = Depends(get_db),
    name: str = Form(...),
    asset_symbol: str = Form(""),
    timeframe: str = Form(""),
    traderspost_webhook_url: str = Form(""),
    mode: str = Form(""),
) -> RedirectResponse:
    """Edit a strategy's core fields after creation. strategy_id is immutable
    (it is the webhook path + LuxAlgo alert key) — delete/recreate to change it.
    """
    result = await db.execute(
        select(Strategy).where(Strategy.strategy_id == strategy_id)
    )
    strategy = result.scalar_one_or_none()
    if strategy is None:
        return redirect("/ui/strategies", flash="Estrategia no encontrada", category="error")

    strategy.name = name.strip() or strategy.name
    strategy.asset_symbol = (asset_symbol.strip() or None)
    strategy.timeframe = (timeframe.strip() or None)
    strategy.traderspost_webhook_url = (traderspost_webhook_url.strip() or None)

    # Keep the dispatch URL in sync on the profile (ConfigResolver reads it there).
    prof_res = await db.execute(
        select(StrategyProfile).where(StrategyProfile.strategy_id == strategy_id)
    )
    profile = prof_res.scalar_one_or_none()
    if profile is None:
        profile = StrategyProfile(strategy_id=strategy_id)
        db.add(profile)
    profile.traderspost_webhook_url = (traderspost_webhook_url.strip() or None)
    if mode in ("paper", "micro", "limited_live", "live"):
        profile.mode = mode

    await AuditService().log(
        db, actor="admin", action="UPDATE", object_type="Strategy",
        object_id=strategy_id,
        new_value={"name": strategy.name, "asset_symbol": strategy.asset_symbol,
                   "timeframe": strategy.timeframe, "mode": profile.mode},
        reason="strategy core fields edited via UI",
    )
    await db.commit()
    return redirect(f"/ui/strategies/{strategy_id}", flash="Estrategia actualizada")


@router.post("/ui/strategies/{strategy_id}/sltp")
async def update_sltp(
    request: Request,
    strategy_id: str,
    db: AsyncSession = Depends(get_db),
    sl_atr_multiplier: str = Form(""),
    tp_atr_multiplier: str = Form(""),
    sl_mode: str = Form("atr"),
    backstop_points: str = Form(""),
    tp_mode: str = Form("unico"),
    tp_nominal_long: str = Form(""),
    tp_nominal_short: str = Form(""),
) -> RedirectResponse:
    """R-obs-2c — SL/TP en ×ATR o PUNTOS FIJOS, elegible desde la UI (hay
    estrategias que piden ×ATR y estrategias que piden stop fijo; los 4
    perfiles HEREDAN este bracket tal cual).

    SL: sl_mode "atr" → sl_atr_multiplier y se APAGA el backstop;
        sl_mode "pts" → pipeline_config_json.backstop_points (el mismo campo
        que aplica el Motor de Riesgo; el SL×ATR queda de fallback).
    TP: tp_mode "nominal" → tp_nominal_long/short (×ATR por lado, p99);
        "unico" → tp_atr_multiplier y se apagan los nominales;
        "off" → sin TP (puede fallar con oto-orders-not-supported).
    TP en puntos fijos NO existe por diseño (validado 2026-07-04): un TP
    fijo se estrecha relativo a la volatilidad justo cuando las ganadoras
    corren — dispararía antes que LuxAlgo. El backstop sí es fijo porque
    capa pérdida (más ancho nunca corta ganadoras).
    """
    prof_res = await db.execute(
        select(StrategyProfile).where(StrategyProfile.strategy_id == strategy_id)
    )
    profile = prof_res.scalar_one_or_none()
    if profile is None:
        profile = StrategyProfile(strategy_id=strategy_id)
        db.add(profile)

    def _pos(value: str) -> float | None:
        v = (value or "").strip()
        if not v:
            return None
        try:
            f = float(v)
            return f if f > 0 else None
        except ValueError:
            return None

    cfg = dict(profile.pipeline_config_json or {})
    antes = {"sl_atr_multiplier": (float(profile.sl_atr_multiplier)
                                   if profile.sl_atr_multiplier else None),
             "tp_atr_multiplier": (float(profile.tp_atr_multiplier)
                                   if profile.tp_atr_multiplier else None),
             **{k: cfg.get(k) for k in ("backstop_points", "tp_nominal_long",
                                        "tp_nominal_short")}}

    if sl_mode == "pts":
        bk = _pos(backstop_points)
        if bk is None:
            return redirect(
                f"/ui/strategies/{strategy_id}",
                flash="SL en puntos fijos requiere un valor > 0",
                category="error")
        cfg["backstop_points"] = bk
    else:
        profile.sl_atr_multiplier = _pos(sl_atr_multiplier)
        cfg.pop("backstop_points", None)

    if tp_mode == "nominal":
        tpl, tps = _pos(tp_nominal_long), _pos(tp_nominal_short)
        if tpl is None or tps is None:
            return redirect(
                f"/ui/strategies/{strategy_id}",
                flash="TP nominal requiere valor > 0 para largos Y cortos",
                category="error")
        cfg["tp_nominal_long"], cfg["tp_nominal_short"] = tpl, tps
    elif tp_mode == "off":
        cfg.pop("tp_nominal_long", None)
        cfg.pop("tp_nominal_short", None)
        profile.tp_atr_multiplier = None
    else:                                   # "unico" (legacy, retrocompat)
        profile.tp_atr_multiplier = _pos(tp_atr_multiplier)
        cfg.pop("tp_nominal_long", None)
        cfg.pop("tp_nominal_short", None)

    profile.pipeline_config_json = cfg or None
    despues = {"sl_atr_multiplier": (float(profile.sl_atr_multiplier)
                                     if profile.sl_atr_multiplier else None),
               "tp_atr_multiplier": (float(profile.tp_atr_multiplier)
                                     if profile.tp_atr_multiplier else None),
               **{k: cfg.get(k) for k in ("backstop_points",
                                          "tp_nominal_long",
                                          "tp_nominal_short")}}
    await AuditService().log(
        db, actor="admin", action="UPDATE", object_type="StrategyProfile",
        object_id=strategy_id, old_value=antes, new_value=despues,
        reason=f"SL/TP editados via UI (sl_mode={sl_mode}, tp_mode={tp_mode})",
    )
    await db.commit()
    return redirect(
        f"/ui/strategies/{strategy_id}", flash="SL/TP actualizados",
    )


@router.post("/ui/strategies/{strategy_id}/scale-entry")
async def update_scale_entry(
    request: Request,
    strategy_id: str,
    db: AsyncSession = Depends(get_db),
    scale_entry_mode: str = Form("design_only"),
    levels: str = Form(""),
    quantities: str = Form(""),
    max_micro_contracts: str = Form(""),
    scale_stop_mode: str = Form("common_position_stop"),
) -> RedirectResponse:
    """Compras escalonadas — edita niveles/cantidades/max en
    pipeline_config_json['scale_entry'] PRESERVANDO el mode vigente (NX-11).

    El motor escalonado SÍ existe (PayloadBuilder.build_scaled + dispatch
    multi-leg); la EJECUCIÓN se activa/desactiva con
    scripts/set_scale_execution.py (mode execute/design_only), nunca desde
    este form. 'enabled' no es un modo válido del vocabulario
    (design_only/execute/live/off) y se rechaza."""
    if scale_entry_mode == "enabled":
        return redirect(
            f"/ui/strategies/{strategy_id}",
            flash="'enabled' no es un modo valido (usa design_only/off aqui; "
                  "la ejecucion se activa con scripts/set_scale_execution.py).",
            category="error",
        )
    prof_res = await db.execute(
        select(StrategyProfile).where(StrategyProfile.strategy_id == strategy_id)
    )
    profile = prof_res.scalar_one_or_none()
    if profile is None:
        profile = StrategyProfile(strategy_id=strategy_id)
        db.add(profile)

    cfg = dict(profile.pipeline_config_json or {})
    before = cfg.get("scale_entry")
    if scale_entry_mode == "off":
        cfg.pop("scale_entry", None)
        se = None
    else:
        try:
            maxm = int(max_micro_contracts) if str(max_micro_contracts).strip() else None
        except ValueError:
            maxm = None
        # NX-11: preservar el mode vigente — guardar niveles/cantidades desde
        # la UI NO debe apagar una ejecucion activada por script.
        prev_mode = (before or {}).get("mode")
        se = {
            "mode": prev_mode if prev_mode in ("execute", "live") else "design_only",
            "levels": _parse_floats(levels),
            "quantities": _parse_ints(quantities),
            "max_micro_contracts": maxm,
            "stop_mode": "common_position_stop",
        }
        cfg["scale_entry"] = se
    profile.pipeline_config_json = cfg or None

    await AuditService().log(
        db, actor="admin", action="UPDATE", object_type="StrategyProfile",
        object_id=strategy_id, old_value={"scale_entry": before},
        new_value={"scale_entry": se}, reason="scale_entry edit (mode preserved)",
    )
    await db.commit()
    if se is None:
        msg = "Scale Entry quitado"
    elif se["mode"] in ("execute", "live"):
        msg = f"Scale Entry guardado — mode={se['mode']} PRESERVADO (EJECUTA ⚠)"
    else:
        msg = "Scale Entry (diseno) guardado"
    return redirect(f"/ui/strategies/{strategy_id}", flash=msg)


@router.post("/ui/strategies/{strategy_id}/profiles")
async def update_profiles(
    request: Request,
    strategy_id: str,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Perfiles de riesgo (tiers) — deltas sobre la base, en
    pipeline_config_json['profiles']. Cada perfil hereda la base y solo overridea
    lo que se rellena (normalmente las cantidades por pierna). Hasta 8 slots
    (la UI muestra 4). Vacío en un campo = hereda de la base."""
    form = await request.form()
    prof_res = await db.execute(
        select(StrategyProfile).where(StrategyProfile.strategy_id == strategy_id)
    )
    profile = prof_res.scalar_one_or_none()
    if profile is None:
        profile = StrategyProfile(strategy_id=strategy_id)
        db.add(profile)

    def _num(raw):
        raw = (raw or "").strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    profiles: list[dict] = []
    warn = False
    for i in range(8):  # UI shows 4; support up to 8
        name = (form.get(f"p{i}_name") or "").strip()
        enabled = form.get(f"p{i}_enabled") == "1"
        webhook = (form.get(f"p{i}_webhook") or "").strip()
        qty_raw = form.get(f"p{i}_quantities")
        max_raw = (form.get(f"p{i}_max") or "").strip()
        note = (form.get(f"p{i}_note") or "").strip()
        dry = form.get(f"p{i}_dry_run") == "1"
        levels_raw = form.get(f"p{i}_levels")
        sl_raw = form.get(f"p{i}_sl")
        tp_raw = form.get(f"p{i}_tp")
        # Skip a completely empty slot
        if not (name or webhook or enabled or (qty_raw or "").strip()):
            continue
        p: dict = {"name": name or f"perfil{i + 1}", "enabled": enabled}
        if webhook:
            p["webhook_url"] = webhook
        if (qty_raw or "").strip():
            p["quantities"] = _parse_ints(qty_raw)
        if max_raw:
            try:
                p["max_contracts"] = max(0, int(float(max_raw)))
            except ValueError:
                pass
        if note:
            p["note"] = note
        if dry:
            p["dry_run"] = True
        if (levels_raw or "").strip():
            p["levels"] = _parse_floats(levels_raw)
        sl = _num(sl_raw)
        if sl:
            p["sl_atr_multiplier"] = sl
        tp = _num(tp_raw)
        if tp:
            p["tp_atr_multiplier"] = tp
        if enabled and not webhook:
            warn = True
        profiles.append(p)

    cfg = dict(profile.pipeline_config_json or {})
    before = cfg.get("profiles")
    if profiles:
        cfg["profiles"] = profiles
    else:
        cfg.pop("profiles", None)
    profile.pipeline_config_json = cfg or None
    profile.version = (profile.version or 1) + 1
    profile.updated_by = "admin"

    await AuditService().log(
        db, actor="admin", action="UPDATE", object_type="StrategyProfile",
        object_id=strategy_id, old_value={"profiles": before},
        new_value={"profiles": profiles}, reason="risk profiles",
    )
    await db.commit()
    msg = f"Perfiles guardados ({len(profiles)})."
    if warn:
        msg += " ⚠ Algún perfil habilitado sin webhook propio: heredará el de la base."
    return redirect(
        f"/ui/strategies/{strategy_id}", flash=msg,
        category="warning" if warn else "success",
    )


@router.post("/ui/strategies/{strategy_id}/status")
async def change_status(
    request: Request,
    strategy_id: str,
    db: AsyncSession = Depends(get_db),
    new_status: str = Form(...),
    reason: str = Form(""),
) -> RedirectResponse:
    if new_status not in _VALID_STATUSES:
        return redirect(
            f"/ui/strategies/{strategy_id}",
            flash=f"Status inválido: {new_status}", category="error",
        )

    result = await db.execute(
        select(Strategy).where(Strategy.strategy_id == strategy_id)
    )
    strategy = result.scalar_one_or_none()
    if strategy is None:
        return redirect("/ui/strategies", flash="Estrategia no encontrada", category="error")

    # quarantine/retire require a reason
    if new_status in ("quarantined", "retired") and not reason.strip():
        return redirect(
            f"/ui/strategies/{strategy_id}",
            flash=f"'{new_status}' requiere un motivo", category="error",
        )

    old_status = strategy.status
    strategy.status = new_status
    # enabled follows execution-capable statuses
    strategy.enabled = new_status in ("shadow", "paper", "micro", "limited_live", "live")
    if new_status == "retired":
        strategy.retired_at = datetime.now(timezone.utc)
        strategy.retired_reason = reason or None

    await AuditService().log_strategy_change(
        db, actor="admin", strategy_id=strategy_id,
        old_data={"status": old_status}, new_data={"status": new_status},
        action="STATUS_CHANGE", reason=reason or None,
    )
    await db.commit()
    return redirect(
        f"/ui/strategies/{strategy_id}",
        flash=f"Status: {old_status} → {new_status}",
    )


# ---------------------------------------------------------------------------
# Clone
# ---------------------------------------------------------------------------

@router.get("/ui/strategies/{strategy_id}/clone", response_class=HTMLResponse)
async def clone_form(
    request: Request, strategy_id: str, db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    result = await db.execute(
        select(Strategy).where(Strategy.strategy_id == strategy_id)
    )
    source = result.scalar_one_or_none()
    if source is None:
        return redirect("/ui/strategies", flash="Estrategia no encontrada", category="error")
    return await render(
        request, "strategy_clone_form.html",
        {"source": source, "assets": await _assets(db)}, db=db,
    )


@router.post("/ui/strategies/{strategy_id}/clone")
async def clone_strategy(
    request: Request,
    strategy_id: str,
    db: AsyncSession = Depends(get_db),
    new_strategy_id: str = Form(...),
    asset_symbol: str = Form(""),
    traderspost_webhook_url: str = Form(""),
) -> RedirectResponse:
    src_res = await db.execute(
        select(Strategy).where(Strategy.strategy_id == strategy_id)
    )
    source = src_res.scalar_one_or_none()
    if source is None:
        return redirect("/ui/strategies", flash="Fuente no encontrada", category="error")

    dup = await db.execute(
        select(Strategy).where(Strategy.strategy_id == new_strategy_id)
    )
    if dup.scalar_one_or_none() is not None:
        return redirect(
            f"/ui/strategies/{strategy_id}/clone",
            flash=f"strategy_id '{new_strategy_id}' ya existe", category="error",
        )

    clone = Strategy(
        strategy_id=new_strategy_id,
        name=f"{source.name} (clon)",
        source=source.source,
        asset_symbol=asset_symbol or source.asset_symbol,
        timeframe=source.timeframe,
        strategy_type=source.strategy_type,
        status="candidate",  # clones always start in candidate
        enabled=False,
        traderspost_webhook_url=traderspost_webhook_url or None,
        template_id=source.template_id,
    )
    # NX-20/NX-22: token propio desde el nacimiento, guardado como hash.
    clone_token = secrets.token_urlsafe(24)
    clone.webhook_token_hash = hash_token(
        clone_token, app_settings.WEBHOOK_TOKEN_SALT
    )
    db.add(clone)

    # Clone the strategy profile config
    src_prof = await db.execute(
        select(StrategyProfile).where(StrategyProfile.strategy_id == strategy_id)
    )
    sp = src_prof.scalar_one_or_none()
    if sp is not None:
        # NX-20 — copiar pipeline_config_json SANEADO: la calibración viaja
        # (windows/filters/regime/guardrails/score/scale/dedup/reserva), pero
        # el clon no hereda cuentas ni ejecución armada.
        cfg = dict(sp.pipeline_config_json or {})
        cfg.pop("profiles", None)              # webhooks de cuentas: fuera
        se = cfg.get("scale_entry")
        if isinstance(se, dict) and se.get("mode") in ("execute", "live"):
            se = dict(se)
            se["mode"] = "design_only"         # nunca nace ejecutando
            cfg["scale_entry"] = se
        db.add(StrategyProfile(
            strategy_id=new_strategy_id,
            mode=sp.mode,
            sl_atr_multiplier=sp.sl_atr_multiplier,
            tp_atr_multiplier=sp.tp_atr_multiplier,
            atr_period=sp.atr_period,
            atr_timeframe=sp.atr_timeframe,
            traderspost_webhook_url=traderspost_webhook_url or None,
            # NX-20: el clon nace desarmado, herede lo que herede la fuente.
            dry_run=True,
            traderspost_enabled=False,
            pipeline_config_json=cfg or None,
        ))

    await AuditService().log(
        db, actor="admin", action="CLONE", object_type="Strategy",
        object_id=new_strategy_id,
        new_value={"cloned_from": strategy_id},
        reason=f"cloned from {strategy_id}",
    )
    await db.commit()
    return redirect(
        f"/ui/strategies/{new_strategy_id}",
        flash=f"Clonada desde '{strategy_id}' → '{new_strategy_id}' — token "
              f"webhook (cópialo YA): {clone_token}",
    )


# ---------------------------------------------------------------------------
# Batch action
# ---------------------------------------------------------------------------

@router.post("/ui/strategies/batch-action")
async def batch_action(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    form = await request.form()
    action = form.get("action", "")
    selected = form.getlist("selected")

    action_map = {
        "pause": "paused", "resume": "paper",
        "shadow": "shadow", "quarantine": "quarantined", "retire": "retired",
    }
    new_status = action_map.get(action)
    if not new_status or not selected:
        return redirect("/ui/strategies", flash="Acción o selección inválida", category="error")

    audit = AuditService()
    count = 0
    for sid in selected:
        res = await db.execute(select(Strategy).where(Strategy.strategy_id == sid))
        strat = res.scalar_one_or_none()
        if strat is None:
            continue
        old = strat.status
        strat.status = new_status
        strat.enabled = new_status in ("shadow", "paper", "micro", "limited_live", "live")
        await audit.log_strategy_change(
            db, actor="admin", strategy_id=sid,
            old_data={"status": old}, new_data={"status": new_status},
            action="STATUS_CHANGE", reason=f"batch {action}",
        )
        count += 1

    await db.commit()
    return redirect("/ui/strategies", flash=f"{count} estrategia(s) → {new_status}")
