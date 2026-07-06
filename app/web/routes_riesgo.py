"""Pestaña Riesgo — front-end del Motor de Riesgo (estudio offline).

Candados de arquitectura:
  1. CERO segundo cálculo: el motor corre TAL CUAL en subproceso
     (scripts.nt_riesgo integrar/calcular) y esta capa solo LEE sus outputs
     (MotorRiesgo/<clave>/manifest.json, runs/estudios_*.json,
     recomendacion_*.json, heatmap_*.png, Riesgo_*.md).
  2. Read-only salvo subir+integrar+calcular. APLICAR la recomendación a la
     estrategia viva sigue siendo un paso aparte (pestaña Estrategias —
     enlazada: misma estrategia, dos vistas).
  3. Estrategia NUEVA de primera clase: subir la lista de una estrategia que
     no está en el manifest la da de alta (símbolo detectado del nombre del
     CSV vía lab_manifest.csv_instrument; el mapeo se confirma por UI = el
     `lab_manifest propose --confirm` hecho web) y queda como una estrategia
     estudiada más.
  4. Reusa los patrones del visor del Lab: upload validado con el parser
     real, job en segundo plano con polling, manifest compartido.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
)
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.web.common import render
import app.web.routes_lab as routes_lab                # manifest compartido
from scripts.lab_manifest import MICRO_TO_LAB, csv_instrument
from scripts.mr_report import fmt_stop                 # FX en ticks/$ (P1-2)

router = APIRouter()

# Patchables en tests (el subproceso del motor recibe MOTOR_RIESGO_DIR por env)
MOTOR_DIR = Path(os.environ.get("MOTOR_RIESGO_DIR") or "MotorRiesgo")
TRADES_DIR = Path("ListaDeOperaciones")

_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Jobs de calcular en segundo plano: {clave: {status, tail, ...}}
JOBS: dict[str, dict] = {}

# P1-4 — integrar SERIALIZADO por estrategia: dos subidas simultáneas de la
# misma clave no compiten por master.csv/manifest (last-writer-wins mudo).
_INTEGRAR_LOCKS: dict[str, asyncio.Lock] = {}


def _lock_integrar(strategy: str) -> asyncio.Lock:
    return _INTEGRAR_LOCKS.setdefault(strategy, asyncio.Lock())


# ---------------------------------------------------------------------------
# Mapeo estrategia (manifest) ↔ carpeta del motor (MotorRiesgo/<clave>)
# ---------------------------------------------------------------------------

def derive_codigo(strategy_id: str, instrument: str) -> str:
    """Código de la carpeta del motor desde el strategy_id del manifest:
    `ES5m_ConfNormal_TC_TSR` → `ConfNormal_TC_TSR` (se quita el prefijo
    <SYM><tf>_). Ids sin ese patrón: el instrumento pelado → `default`;
    otro id → tal cual (la guardia de doble-prefijo del motor vigila)."""
    m = re.match(r"^[A-Za-z0-9]+_(?P<code>.+)$", strategy_id)
    if m and not m.group("code").upper().startswith(instrument.upper() + "_"):
        return m.group("code")
    if strategy_id == instrument:
        return "default"
    return strategy_id


def clave_de(strategy_id: str, instrument: str) -> str:
    return f"{instrument}_{derive_codigo(strategy_id, instrument)}"


def _instrument_de(strategy_id: str, manifest: dict) -> str | None:
    entry = manifest.get(strategy_id)
    return entry["instrument"] if entry else None


# ---------------------------------------------------------------------------
# Lectura de outputs del motor (solo lectura — el cálculo es del CLI)
# ---------------------------------------------------------------------------

def _motor_manifest(clave: str) -> dict | None:
    p = MOTOR_DIR / clave / "manifest.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def _latest_run_file(clave: str, patron: str) -> Path | None:
    hits = sorted((MOTOR_DIR / clave / "runs").glob(patron))
    return hits[-1] if hits else None


def _latest_estudio(clave: str) -> dict | None:
    p = _latest_run_file(clave, "estudios_*.json")
    if p is None:
        return None
    try:
        res = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    res["_fecha"] = p.stem.replace("estudios_", "")
    return res


def _comparacion(res: dict) -> list[dict] | None:
    """Tarjetas KPI base → recomendada con Δ, computadas en el SERVIDOR
    (nada de métricas en JS). El número que manda es el OOS."""
    reco = res.get("recomendacion")
    if not reco:
        return None
    b_t = res["linea_base"]["total"]
    b_out = res["linea_base"]["out"]
    r_t = reco["metricas"]["total"]
    conf = reco["confianza_oos"]

    def kpi(label, b, r, fmt, mejor_alto=True, destacar=False):
        delta = (round(r - b, 2) if b is not None and r is not None else None)
        good = (None if delta is None or delta == 0
                else (delta > 0) == mejor_alto)
        return {"label": label, "base": b, "reco": r, "delta": delta,
                "fmt": fmt, "good": good, "destacar": destacar}

    return [
        kpi("Net total", b_t.get("net_usd"), r_t.get("net_usd"), "usd"),
        kpi("PF OOS ★", b_out.get("pf"), conf.get("pf_out"), "num",
            destacar=True),
        kpi("Max Drawdown", b_t.get("max_dd_usd"), r_t.get("max_dd_usd"),
            "usd", mejor_alto=False),
        kpi("Peor trade", b_t.get("peor_trade_usd"),
            r_t.get("peor_trade_usd"), "usd"),
        kpi("Participación %", 100.0,
            reco["metricas"].get("participacion_pct"), "pct"),
        kpi("WinRate %", b_t.get("wr_pct"), r_t.get("wr_pct"), "pct"),
    ]


def _activacion_json(reco: dict) -> dict:
    """El fragmento LISTO para pegar en el pipeline_config_json de la
    estrategia (pestaña Estrategias): backstop + TP nominal + escalera +
    cancel_after. short_size_factor es el afinable del operador (no lo fija
    el estudio) — se aplica aparte."""
    esc = reco.get("escalera") or {}
    piernas = sorted(esc.get("piernas") or [], key=lambda p: p["depth_atr"])
    tp = reco.get("tp_nominal_atr") or {}
    out: dict = {}
    if reco.get("backstop"):
        out["backstop_points"] = reco["backstop"]["pts"]
    if tp.get("long"):
        out["tp_nominal_long"] = tp["long"]
    if tp.get("short"):
        out["tp_nominal_short"] = tp["short"]
    if reco.get("cancel_after_seconds"):
        out["entry_reserve_timeout_seconds"] = reco["cancel_after_seconds"]
    if piernas:
        out["scale_entry"] = {
            "mode": "execute",
            "quantities": [0] + [p["micros"] for p in piernas],
            "levels": [p["depth_atr"] for p in piernas],
            "max_micro_contracts": esc.get("total_micros", 10),
        }
    return out


def _motivo_sin_reco(res: dict) -> dict:
    """P1-1 — cuando el walk-forward no valida nada (6E/6J/YM), la sección
    de estudio NUNCA queda en blanco: motivo honesto + las top configs
    aprobadas como referencia, marcadas 'no validadas por OOS'."""
    rob = res.get("robustez") or {}
    tabla = rob.get("tabla") or []
    partes: list[str] = []
    if not (res.get("backstop") or {}).get("optimo"):
        partes.append("sin catástrofe que atajar — ningún backstop supera "
                      "el score del crudo (el nativo ya es de bajo riesgo)")
    veredictos = [t.get("veredicto") or "" for t in tabla]
    if any(v == "sin datos comparables" for v in veredictos):
        partes.append("OOS sin pérdidas en el crudo → ΔPF no computable "
                      "fuera de muestra (no validable, que no es malo)")
    if veredictos and not any(v.startswith("validado") for v in veredictos):
        if any(v == "no generaliza OOS" for v in veredictos):
            partes.append("ninguna config supera al crudo fuera de muestra "
                          "(el nativo domina)")
        if any(v.startswith("mixto") for v in veredictos):
            partes.append("candidatas rentables pero pierden en una mitad "
                          "(inestables)")
    flags = {f for t in tabla for f in (t.get("flags") or [])}
    if "n_bajo" in flags or "robustez_fragil" in flags:
        partes.append("muestra insuficiente (bandera n bajo)")
    aprobadas = [c for c in res.get("configs") or []
                 if (c.get("gate") or {}).get("estado") == "aprobada"
                 and "informativo" not in (c.get("etiquetas") or [])]
    top = sorted(aprobadas,
                 key=lambda c: -((c.get("gate") or {}).get("score")
                                 or -9e18))[:3]
    return {
        "motivo": ("; ".join(partes)
                   or "el walk-forward no validó ninguna candidata"),
        "top": [{
            "nombre": c.get("nombre"),
            "net_usd": (c.get("total") or {}).get("net_usd"),
            "pf": (c.get("total") or {}).get("pf"),
            "max_dd_usd": (c.get("total") or {}).get("max_dd_usd"),
            "participacion_pct": c.get("participacion_pct"),
            "score": (c.get("gate") or {}).get("score"),
        } for c in top],
    }


def _estudio_ctx(clave: str) -> dict | None:
    res = _latest_estudio(clave)
    if res is None:
        return None
    reco = res.get("recomendacion")
    corte = res.get("corte_fills") or {}
    configs = res.get("configs") or []
    heat = _latest_run_file(clave, "heatmap_*.png")
    md = _latest_run_file(clave, "Riesgo_*.md")
    activo = (res.get("meta") or {}).get("activo") or clave.split("_")[0]
    return {
        "fecha": res.get("_fecha"),
        "base": res["linea_base"]["total"],
        "base_pf_oos": res["linea_base"]["out"].get("pf"),
        "comparacion": _comparacion(res),
        "reco": reco,
        "sin_reco": None if reco else _motivo_sin_reco(res),
        # P1-2: backstop legible por instrumento (FX en ticks/$ — display,
        # el cálculo del stop en L5 no cambia)
        "backstop_fmt": (fmt_stop(activo, reco["backstop"]["pts"],
                                  reco["backstop"]["usd_por_mini"])
                         if reco and reco.get("backstop") else None),
        "activacion": (json.dumps(_activacion_json(reco), indent=2,
                                  ensure_ascii=False) if reco else None),
        "corte": {"cancel_after_s": corte.get("cancel_after_s"),
                  "tope_natural_atr": corte.get("tope_natural_atr")},
        "ls": (res.get("ls") or {}).get("lectura"),
        # P1b — gestión por lado (estructural; independiente del elegido)
        "gestion_lado": (res.get("gestion_lado") or {}).get("recomendacion"),
        "n_configs": len(configs),
        "n_aprobadas": sum(1 for c in configs
                           if c.get("gate", {}).get("estado") == "aprobada"),
        "elegido": ((res.get("robustez") or {}).get("elegido") or {}
                    ).get("nombre"),
        "veredicto": (reco or {}).get("confianza_oos", {}).get("veredicto"),
        "flags": (reco or {}).get("confianza_oos", {}).get("flags") or [],
        "heatmap": heat.name if heat else None,
        "md": md.name if md else None,
        "meta": res.get("meta") or {},
    }


# ---------------------------------------------------------------------------
# Página
# ---------------------------------------------------------------------------

@router.get("/ui/riesgo", response_class=HTMLResponse)
async def riesgo_page(request: Request, strategy: str | None = None,
                      db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    manifest = routes_lab.load_manifest()
    groups: dict[str, list[str]] = {}
    for k, e in sorted(manifest.items(),
                       key=lambda kv: (kv[1]["instrument"], kv[0])):
        groups.setdefault(e["instrument"], []).append(k)

    key = strategy if (strategy and _KEY_RE.match(strategy)
                       and strategy in manifest) else None
    if key is None and manifest:
        key = next(iter(sorted(
            manifest, key=lambda k: (manifest[k]["instrument"], k))))

    # Estrategias VIVAS de la DB (las mismas de la pestaña Estrategias):
    # para el alta de una nueva y para el enlace estudio ↔ config en vivo.
    vivas: list[dict] = []
    try:
        from app.models.strategy import Strategy
        rows = (await db.execute(select(Strategy))).scalars().all()
        vivas = [{"strategy_id": s.strategy_id,
                  "asset_symbol": s.asset_symbol or "",
                  "instrument": MICRO_TO_LAB.get(
                      (s.asset_symbol or "").strip().upper()),
                  "status": s.status}
                 for s in rows]
    except Exception:
        pass
    vivas_ids = {v["strategy_id"] for v in vivas}

    ctx: dict = {
        "groups": groups, "key": key, "manifest_entry": None,
        "motor": None, "avisos": [], "estudio": None, "clave": None,
        "job": None, "link_vivo": None,
        "vivas_nuevas": sorted([v for v in vivas
                                if v["strategy_id"] not in manifest
                                and v["instrument"]],
                               key=lambda v: v["strategy_id"]),
    }
    if key:
        entry = manifest[key]
        instrument = entry["instrument"]
        clave = clave_de(key, instrument)
        motor = _motor_manifest(clave)
        ctx.update({
            "manifest_entry": {"csv": Path(entry["csv"]).name,
                               "instrument": instrument,
                               "confirmed": bool(entry.get("confirmed"))},
            "clave": clave,
            "motor": motor,
            "estudio": _estudio_ctx(clave),
            "job": (JOBS.get(clave) or {}).get("status"),
            "link_vivo": (f"/ui/strategies/{key}"
                          if key in vivas_ids else None),
        })
        if motor:
            # Identidad del master + avisos (el MISMO helper del CLI)
            from scripts.nt_riesgo import _avisos_master
            try:
                ctx["avisos"] = _avisos_master(
                    motor, instrument, date.today(), TRADES_DIR,
                    MOTOR_DIR / clave / "snapshots")
            except (KeyError, ValueError):
                ctx["avisos"] = []
    return await render(request, "riesgo.html", ctx)


# ---------------------------------------------------------------------------
# Subir lista (existente o estrategia NUEVA) → integrar (subproceso awaited)
# ---------------------------------------------------------------------------

def _integrar_cmd(csv_path: Path, codigo: str, activo: str) -> list[str]:
    cmd = [sys.executable, "-m", "scripts.nt_riesgo", "integrar",
           str(csv_path), "--codigo", codigo, "--activo", activo]
    if _stitch():
        cmd.append("--stitch-db")
    return cmd


def _calc_cmd(clave: str) -> list[str]:
    cmd = [sys.executable, "-m", "scripts.nt_riesgo", "calcular", clave]
    if _stitch():
        cmd.append("--stitch-db")
    return cmd


def _stitch() -> bool:
    v = (os.environ.get("MR_CALC_STITCH")
         or os.environ.get("LAB_RECALC_STITCH") or "")
    return v.lower() in ("1", "true")


def _motor_env() -> dict:
    return {**os.environ, "MOTOR_RIESGO_DIR": str(MOTOR_DIR)}


async def _run_motor(cmd: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT, env=_motor_env())
    out, _ = await proc.communicate()
    return proc.returncode, (out or b"").decode("utf-8",
                                                errors="replace")[-3000:]


@router.post("/ui/riesgo/upload")
async def riesgo_upload(strategy: str = Form(...),
                        file: UploadFile = File(...)) -> JSONResponse:
    """Sube la lista de operaciones y corre `integrar` (cuadre al dólar
    bloqueante, guardia de doble-prefijo — los errores del motor se muestran
    tal cual). Estrategia fuera del manifest = ALTA NUEVA: símbolo detectado
    del nombre original del CSV, mapeo confirmado por UI."""
    strategy = (strategy or "").strip()
    if not _KEY_RE.match(strategy):
        return JSONResponse({"error": "strategy_id inválido (usa letras/"
                                      "números/_/-, máx 64)"},
                            status_code=400)
    manifest = routes_lab.load_manifest()
    entry = manifest.get(strategy)
    instrument = (entry["instrument"] if entry
                  else csv_instrument(file.filename or ""))
    if not instrument:
        return JSONResponse(
            {"error": "no pude detectar el símbolo del nombre del CSV "
                      "(patrón _<SYM>1!_ de LuxAlgo, p. ej. "
                      "…_CME_MINI_ES1!_2026-07-04_x.csv) — estrategia nueva "
                      "necesita el export con su nombre original"},
            status_code=400)

    raw = await file.read()
    if len(raw) > 20_000_000:
        return JSONResponse({"error": "archivo demasiado grande"},
                            status_code=400)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    TRADES_DIR.mkdir(exist_ok=True)
    dest = TRADES_DIR / f"upload_{strategy}_{ts}.csv"
    dest.write_bytes(raw)

    # Validación con el parser REAL antes de tocar manifest/motor
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

    # integrar (el motor manda: doble-prefijo, cuadre al dólar, TZ) —
    # SERIALIZADO por estrategia (P1-4): el lock cubre subproceso + manifest.
    clave = clave_de(strategy, instrument)
    async with _lock_integrar(strategy):
        rc, tail = await _run_motor(
            _integrar_cmd(dest, derive_codigo(strategy, instrument),
                          instrument))
        if rc != 0:
            dest.unlink(missing_ok=True)
            return JSONResponse(
                {"error": "integrar falló", "detalle": tail.strip()[-800:]},
                status_code=400)

        # Manifest: actualizar CSV (existente) o ALTA confirmada (nueva) —
        # el `lab_manifest propose --confirm` hecho por UI.
        manifest = routes_lab.load_manifest()      # releer bajo el lock
        entry = manifest.get(strategy)
        rel = dest.as_posix()
        manifest[strategy] = {**(entry or {"candidates": None}),
                              "instrument": instrument, "csv": rel,
                              "confirmed": True}
        mp = routes_lab.LAB_DIR / "lab_manifest.json"
        data = (json.loads(mp.read_text(encoding="utf-8"))
                if mp.exists() else {"version": 1})
        data["entries"] = manifest
        mp.parent.mkdir(exist_ok=True)
        mp.write_text(json.dumps(data, indent=1, ensure_ascii=False),
                      encoding="utf-8")

    motor = _motor_manifest(clave) or {}
    return JSONResponse({
        "ok": True, "clave": clave, "strategy": strategy,
        "nueva": entry is None,
        "n_trades": (motor.get("trades") or {}).get("n", len(trades)),
        "integrado": motor.get("integrado"),
        "hint": "dale Calcular para correr el estudio",
    })


# ---------------------------------------------------------------------------
# Calcular en segundo plano + polling (patrón del recalc del Lab)
# ---------------------------------------------------------------------------

class CalcReq(BaseModel):
    strategy: str


def _resolver_clave(strategy: str) -> str | None:
    if not _KEY_RE.match(strategy or ""):
        return None
    entry = routes_lab.load_manifest().get(strategy)
    if entry is None:
        return None
    return clave_de(strategy, entry["instrument"])


async def _run_calc(clave: str, cmd: list[str]) -> None:
    try:
        rc, tail = await _run_motor(cmd)
        JOBS[clave].update({
            "status": "done" if rc == 0 else "error",
            "rc": rc, "tail": tail,
            "finished": datetime.now().isoformat(timespec="seconds"),
        })
    except Exception as exc:                      # el job NUNCA muere mudo
        JOBS[clave].update({"status": "error", "tail": repr(exc)})


@router.post("/ui/riesgo/calcular", status_code=202)
async def riesgo_calcular(req: CalcReq) -> JSONResponse:
    clave = _resolver_clave(req.strategy)
    if clave is None:
        return JSONResponse({"error": "estrategia fuera del manifest"},
                            status_code=400)
    if _motor_manifest(clave) is None:
        return JSONResponse({"error": "sin listado integrado — sube el "
                                      "CSV primero"}, status_code=409)
    if (JOBS.get(clave) or {}).get("status") == "running":
        return JSONResponse({"error": "ya hay un cálculo corriendo"},
                            status_code=409)
    JOBS[clave] = {"status": "running",
                   "started": datetime.now().isoformat(timespec="seconds"),
                   "tail": ""}
    JOBS[clave]["task"] = asyncio.create_task(
        _run_calc(clave, _calc_cmd(clave)))
    return JSONResponse({"ok": True, "status": "running", "clave": clave},
                        status_code=202)


@router.get("/ui/riesgo/calcular/status")
async def riesgo_calc_status(strategy: str) -> JSONResponse:
    clave = _resolver_clave(strategy)
    if clave is None:
        return JSONResponse({"error": "estrategia fuera del manifest"},
                            status_code=400)
    job = JOBS.get(clave)
    if job is None:
        return JSONResponse({"status": "idle"})
    return JSONResponse({k: v for k, v in job.items() if k != "task"})


# ---------------------------------------------------------------------------
# Artefactos de la última corrida (el motor los generó; aquí solo se sirven)
# ---------------------------------------------------------------------------

@router.get("/ui/riesgo/heatmap")
async def riesgo_heatmap(strategy: str):
    clave = _resolver_clave(strategy)
    if clave is None:
        return JSONResponse({"error": "estrategia fuera del manifest"},
                            status_code=400)
    p = _latest_run_file(clave, "heatmap_*.png")
    if p is None:
        return JSONResponse({"error": "sin heatmap — corre Calcular"},
                            status_code=404)
    return FileResponse(p, media_type="image/png")


@router.get("/ui/riesgo/reporte")
async def riesgo_reporte(strategy: str):
    clave = _resolver_clave(strategy)
    if clave is None:
        return JSONResponse({"error": "estrategia fuera del manifest"},
                            status_code=400)
    p = _latest_run_file(clave, "Riesgo_*.md")
    if p is None:
        return JSONResponse({"error": "sin reporte — corre Calcular"},
                            status_code=404)
    return PlainTextResponse(p.read_text(encoding="utf-8"),
                             media_type="text/markdown; charset=utf-8")
