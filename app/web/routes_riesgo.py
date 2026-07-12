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
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.web.common import flash_messages, render
import app.web.routes_lab as routes_lab                # manifest compartido
from app.web.manifest_store import (               # LAB-1 — fuente única
    _INTEGRAR_LOCKS,
    guardar_manifest as _guardar_manifest,
    lock_integrar as _lock_integrar,
)
from scripts.lab_manifest import MICRO_TO_LAB, csv_instrument
from scripts.mr_sims import proteccion_para_cuenta     # selección PURA (v2)

router = APIRouter()

# Patchables en tests (el subproceso del motor recibe MOTOR_RIESGO_DIR por env)
MOTOR_DIR = Path(os.environ.get("MOTOR_RIESGO_DIR") or "MotorRiesgo")
TRADES_DIR = Path("ListaDeOperaciones")

_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Jobs de calcular en segundo plano: {clave: {status, tail, ...}}
JOBS: dict[str, dict] = {}

# P1-4 — integrar SERIALIZADO por estrategia (last-writer-wins mudo): el lock
# y el store del manifest son COMPARTIDOS con el Lab (app.web.manifest_store).
# `_INTEGRAR_LOCKS` / `_lock_integrar` / `_guardar_manifest` se importan de ahí
# y se conservan como símbolos de este módulo por retrocompat (tests).


# ---------------------------------------------------------------------------
# Cuenta editable (v2-A): persiste en MotorRiesgo/cuenta.json; la selección
# de la protección recomputa al vuelo con proteccion_para_cuenta (pura) —
# el barrido pesado ya está persistido por el motor (cero segundo cálculo).
# ---------------------------------------------------------------------------

CUENTA_DEFAULT = 10_000.0


def _leer_cuenta(clave: str | None = None) -> float:
    """Cuenta editable. R-obs-2: vive POR ESTRATEGIA
    (MotorRiesgo/<clave>/cuenta.json) con fallback al global
    (MotorRiesgo/cuenta.json) y luego al default — cada estrategia puede
    proteger una cuenta distinta."""
    rutas = ([MOTOR_DIR / clave / "cuenta.json"] if clave else []) \
        + [MOTOR_DIR / "cuenta.json"]
    for p in rutas:
        try:
            v = float(json.loads(p.read_text(encoding="utf-8"))["cuenta_usd"])
            if v > 0:
                return v
        except (OSError, ValueError, KeyError):
            continue
    return CUENTA_DEFAULT


# ---------------------------------------------------------------------------
# LOTE RIES-W — ventana de operación: % de trades del backtest FUERA de la
# ventana L2 vigente (participación perdida). Reusa SessionValidator (misma
# lógica que el L2: días %w Sun=0 + ventana horaria, con soporte overnight),
# sobre las `muestras` [dow, minuto_del_día] que persiste el estudio — cero
# recomputación del motor.
# ---------------------------------------------------------------------------

def _pct_trades_fuera(session_cfg: dict | None, muestras: list) -> float | None:
    """% de entradas que caen FUERA de la ventana vigente. None si no hay
    muestras; 0.0 si no hay ventana restrictiva (cubre todo, como el L2)."""
    if not muestras:
        return None
    from datetime import time as _time

    from app.services.session_validator import SessionValidator

    cfg = session_cfg or {}
    wins = cfg.get("windows")
    if wins:
        windows = wins
    elif cfg.get("entry_start") or cfg.get("days_enabled"):
        windows = [{"days": cfg.get("days_enabled", [1, 2, 3, 4, 5]),
                    "start": cfg.get("entry_start", "09:30"),
                    "end": cfg.get("entry_end", "15:45"),
                    "next_day_end": cfg.get("next_day_end", False)}]
    else:
        return 0.0                      # sin ventana L2 restrictiva → cubre todo
    sv = SessionValidator()
    fuera = 0
    for dow, mod in muestras:
        t = _time(int(mod) // 60, int(mod) % 60)
        if not any(sv._window_matches(w, int(dow), t) for w in windows):
            fuera += 1
    return round(100 * fuera / len(muestras), 1)


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


def _kpis_comparacion(b_t: dict, r_t: dict, pf_base, pf_reco,
                      pf_label: str, pf_badge: str,
                      part_reco) -> list[dict]:
    """Las MISMAS 6 tarjetas KPI (Net, PF★, Max DD, Peor, Participación,
    WinRate) como crudo → con config con Δ, computadas en el SERVIDOR —
    los dos estudios (validado OOS y protección in-sample) las ESPEJEAN
    campo por campo; solo cambia la tarjeta ★ de confianza (label+badge)."""
    def kpi(label, b, r, fmt, mejor_alto=True, destacar=False, badge=None):
        delta = (round(r - b, 2) if b is not None and r is not None else None)
        good = (None if delta is None or delta == 0
                else (delta > 0) == mejor_alto)
        return {"label": label, "base": b, "reco": r, "delta": delta,
                "fmt": fmt, "good": good, "destacar": destacar,
                "badge": badge}

    return [
        kpi("Net total", b_t.get("net_usd"), r_t.get("net_usd"), "usd"),
        kpi(pf_label, pf_base, pf_reco, "num", destacar=True,
            badge=pf_badge),
        kpi("Max Drawdown", b_t.get("max_dd_usd"), r_t.get("max_dd_usd"),
            "usd", mejor_alto=False),
        kpi("Peor trade", b_t.get("peor_trade_usd"),
            r_t.get("peor_trade_usd"), "usd"),
        kpi("Participación %", 100.0, part_reco, "pct"),
        kpi("WinRate %", b_t.get("wr_pct"), r_t.get("wr_pct"), "pct"),
    ]


def _comparacion(res: dict) -> list[dict] | None:
    """Estudio 1 (validado): tarjetas crudo → con config; la ★ es el PF
    fuera de muestra (el in-sample nunca decide)."""
    reco = res.get("recomendacion")
    if not reco:
        return None
    return _kpis_comparacion(
        res["linea_base"]["total"], reco["metricas"]["total"],
        res["linea_base"]["out"].get("pf"),
        reco["confianza_oos"].get("pf_out"),
        "PF fuera de muestra (OOS) ★", "validado",
        reco["metricas"].get("participacion_pct"))


def _fmt_unidad(pts, unidades: dict | None, ppt) -> str:
    """R-obs-4 — unidad natural del instrumento + $, con FUENTE ÚNICA en
    Symbol Mapper (symbol_maps.tick_value/tick_size — los mismos que ya
    consume config_resolver; nada duplicado en la estrategia).

    Regla de la referencia: FX (tick_size chico) o $/punto enorme → ticks,
    no puntos (el yen en 'puntos' es ilegible). Sin tick data en el catálogo
    → SOLO el $ (nunca un número engañoso) + aviso de catálogo incompleto."""
    if pts is None:
        return "—"
    usd = round(pts * ppt) if ppt else None
    usd_txt = f"${usd:,.0f}" if usd is not None else "—"
    ts = (unidades or {}).get("tick_size")
    if not ts:
        return f"{usd_txt} (⚠ catálogo incompleto — sin tick_size/" \
               f"tick_value en Symbol Mapper)"
    if ts < 0.01 or (ppt or 0) >= 500:          # FX / $-por-punto enorme
        return f"{round(pts / ts):,} ticks = {usd_txt}"
    return f"{pts:g} pts = {round(pts / ts):,} ticks = {usd_txt}"


def _proteccion_ctx(res: dict, cuenta: float,
                    unidades: dict | None = None) -> dict | None:
    """Estudio 2 (protección de cuenta, in-sample): selección PURA por la
    cuenta editable sobre los combos que el motor YA persistió + las mismas
    6 tarjetas KPI y las mismas palancas (SL, escalera, TP, lado) que el
    estudio validado — espejo campo por campo (R-obs-1: mismas palancas,
    mismas métricas; solo cambia split/gate)."""
    prot = res.get("proteccion")
    if not prot or not prot.get("combos"):
        return None
    b_t = res["linea_base"]["total"]
    pc = proteccion_para_cuenta(prot, cuenta, b_t)
    el = pc["elegido"]
    m = el["metricas"]
    ppt = (res.get("meta") or {}).get("usd_por_punto")
    atr_med = prot.get("atr_mediana_pts")

    def unidad_atr(x_atr):
        if x_atr is None or not atr_med:
            return None
        return _fmt_unidad(x_atr * atr_med, unidades, ppt)

    # 🛑 SL — copy R-obs-3: freno catastrófico, sin decir "backstop" en el
    # título ni cerrar con "sin backstop"
    if el["sl_atr"]:
        sl_txt = (f"SL {el['sl_atr']:g}×ATR — freno catastrófico anclado a "
                  f"la señal (respeta el suelo del MAE de las ganadoras, "
                  f"p95 {prot['suelo_atr']}×ATR; capa el desastre sin sacar "
                  f"ganadores)")
        u = unidad_atr(el["sl_atr"])
        if u:
            sl_txt += f" · ≈ {u} con el ATR mediano"
        if el["backstop_usd"]:
            sl_txt += (f" + stop de $ fijo "
                       f"{_fmt_unidad(el['backstop_usd'] / ppt if ppt else None, unidades, ppt)}")
        else:
            sl_txt += ". Sin stop adicional de $ fijo"
    elif el["backstop_usd"]:
        b_pts = el["backstop_usd"] / ppt if ppt else None
        sl_txt = (f"stop de PRECIO FIJO desde la señal: "
                  f"{_fmt_unidad(b_pts, unidades, ppt)} — capa el desastre; "
                  f"sin SL ×ATR adicional")
    else:
        sl_txt = "sin freno adicional — el crudo ya protege a esta cuenta"

    # 🪜 Escalera — la recomendación REAL (niveles + cantidad), ya no un
    # "sin escalera" fijo
    piernas = (el.get("escalera") or {}).get("piernas") or []
    con_escalera = (len(piernas) > 1
                    or any(p["depth_atr"] > 0 for p in piernas))
    if con_escalera:
        esc_txt = " + ".join(
            f"{p['micros']} micros @ {p['depth_atr']:g}×ATR"
            + (f" (≈ {unidad_atr(p['depth_atr'])})"
               if p["depth_atr"] > 0 and unidad_atr(p["depth_atr"]) else "")
            for p in piernas) + " — anclada a la señal"
    else:
        esc_txt = ("entrada única a la señal — la escalera no mejoró la "
                   "supervivencia a esta cuenta")

    tp = el["tp_por_lado_atr"]
    if tp:
        tp_txt = (f"TP nominal L {tp.get('long')}× / S {tp.get('short')}×ATR "
                  f"— por encima del p99 de cierres de LuxAlgo (bracket que "
                  f"exige TradersPost; se toca rara vez)")
    else:
        tp_txt = ("corrida vieja sin TP nominal — recalcula (el TP nominal "
                  "va siempre)")
    if el["lado"]:
        etq = {"long": "largos", "short": "cortos"}[el["lado"]]
        otro = {"long": "cortos", "short": "largos"}[el["lado"]]
        lado_txt = (f"solo {etq} — bloquear {otro}: el lado eliminado "
                    f"guarda el desastre")
        if prot.get("lado_muestra_chica"):
            lado_txt += (f" (muestra chica: {prot.get('lado_n_malo')} "
                         f"trades en el lado eliminado — valida en demo)")
    else:
        lado_txt = "ambos lados operan (no bloquear ninguno)"
    # R-obs-2b — la ficha de protección ESPEJA las líneas de la validada
    # (mismos datos, números propios): + cancel_after, sizing y confianza.
    ca = ((res.get("corte_fills") or {}).get("cancel_after_s"))
    ca_txt = (f"{int(ca)}s (corte de fills del estudio; máx duro "
              f"TradersPost 3600s)" if con_escalera and ca else
              "no aplica — entrada a la señal, sin piernas límite profundas")
    conf_txt = (f"PF in-sample {m.get('pf')} — sin validar OOS "
                f"(para decidir, NO promesa a futuro)")
    palancas = [
        {"icon": "🛑", "titulo": "SL", "texto": sl_txt},
        {"icon": "🪜", "titulo": "Escalera", "texto": esc_txt},
        {"icon": "🎯", "titulo": "TP", "texto": tp_txt},
        {"icon": "↔", "titulo": "Lado", "texto": lado_txt},
        {"icon": "⏱", "titulo": "cancel_after", "texto": ca_txt},
        {"icon": "📐", "titulo": "Sizing", "texto": "tamaño fijo, sin equity"},
        {"icon": "✅", "titulo": "Confianza", "texto": conf_txt},
    ]
    return {
        **{k: pc[k] for k in ("cuenta_usd", "umbral_alarma_pct", "alarmas",
                              "n_alarmas", "protegido", "efecto", "crudo",
                              "etiqueta", "nota_supervivencia")},
        "elegido": el,
        "suelo_atr": prot.get("suelo_atr"),
        "kpis": _kpis_comparacion(
            b_t, m, b_t.get("pf"), m.get("pf"),
            "PF (in-sample) ★", "sin validar — para decidir",
            el["participacion_pct"]),
        "palancas": palancas,
    }


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


# ---------------------------------------------------------------------------
# Puente Riesgo↔Estrategias (SPEC 2026-07-06) — P1 deriva + P2 aplicar.
# El estudio JAMÁS muta la config viva solo: aplicar es SIEMPRE un acto del
# operador con el diff a la vista (preview → confirmar). Nunca toca el
# kill-switch (mode/dry_run/traderspost/status) y preserva el mode de
# scale_entry (NX-11: aplicar niveles no arma la ejecución escalonada).
# ---------------------------------------------------------------------------

def _num_eq(a, b) -> bool:
    try:
        return (a is not None and b is not None
                and abs(float(a) - float(b)) < 1e-9)
    except (TypeError, ValueError):
        return False


def _se_igual(vivo: dict | None, reco: dict | None) -> bool:
    """Escalera igual = mismos levels/quantities/max. mode y stop_mode son
    del operador (NX-11) y NO cuentan para la deriva."""
    vivo, reco = vivo or {}, reco or {}
    try:
        return ([float(x) for x in (vivo.get("levels") or [])]
                == [float(x) for x in (reco.get("levels") or [])]
                and [int(x) for x in (vivo.get("quantities") or [])]
                == [int(x) for x in (reco.get("quantities") or [])]
                and _num_eq(vivo.get("max_micro_contracts"),
                            reco.get("max_micro_contracts")))
    except (TypeError, ValueError):
        return False


def deriva_estudio(pcfg: dict | None, act: dict | None,
                   fecha: str | None = None,
                   hay_viva: bool = True) -> dict | None:
    """P1 — estado de la config viva frente a la recomendación validada
    (_activacion_json): aplicada / difiere / sin_aplicar /
    sin_estrategia_viva. Sin recomendación → None (sin badge)."""
    if not act:
        return None
    if not hay_viva:
        return {"estado": "sin_estrategia_viva",
                "texto": "sin estrategia viva"}
    pcfg = pcfg or {}
    iguales: list[bool] = []
    presentes: list[bool] = []
    for k in ("backstop_points", "tp_nominal_long", "tp_nominal_short",
              "entry_reserve_timeout_seconds"):
        if k in act:
            iguales.append(_num_eq(pcfg.get(k), act[k]))
            presentes.append(pcfg.get(k) is not None)
    if "scale_entry" in act:
        iguales.append(_se_igual(pcfg.get("scale_entry"),
                                 act["scale_entry"]))
        presentes.append(bool(pcfg.get("scale_entry")))
    suf = f" (estudio {fecha})" if fecha else ""
    if iguales and all(iguales):
        return {"estado": "aplicada", "texto": f"aplicada{suf}"}
    if not any(presentes):
        return {"estado": "sin_aplicar",
                "texto": f"recomendación SIN aplicar{suf}"}
    return {"estado": "difiere", "texto": f"difiere del estudio{suf}"}


async def _viva_y_reco(db: AsyncSession, strategy: str):
    """Resuelve (profile_viva, reco, fecha) o (None, error_response)."""
    strategy = (strategy or "").strip()
    if not _KEY_RE.match(strategy):
        return None, JSONResponse({"error": "strategy_id inválido"},
                                  status_code=400)
    manifest = routes_lab.load_manifest()
    entry = manifest.get(strategy)
    if entry is None:
        return None, JSONResponse({"error": "estrategia fuera del manifest"},
                                  status_code=400)
    res = _latest_estudio(clave_de(strategy, entry["instrument"]))
    reco = (res or {}).get("recomendacion")
    if not reco:
        return None, JSONResponse(
            {"error": "sin recomendación validada — corre Calcular y "
                      "necesita pasar el gate OOS"}, status_code=400)
    from app.models.strategy_profile import StrategyProfile
    prow = (await db.execute(select(StrategyProfile).where(
        StrategyProfile.strategy_id == strategy))).scalar_one_or_none()
    from app.models.strategy import Strategy
    srow = (await db.execute(select(Strategy).where(
        Strategy.strategy_id == strategy))).scalar_one_or_none()
    if srow is None:
        return None, JSONResponse(
            {"error": "sin estrategia viva con este id — dala de alta en "
                      "Estrategias primero (o promuévela desde el estudio)"},
            status_code=400)
    return {"profile": prow, "reco": reco,
            "fecha": (res or {}).get("_fecha")}, None


def _diff_aplicar(pcfg: dict, act: dict, fecha: str | None,
                  sl_atr_vivo, tp_atr_vivo) -> list[dict]:
    """Filas del diff vivo → recomendado (P2 preview). Solo informa; el
    merge real es _merge_activacion."""
    filas: list[dict] = []

    def fila(campo, vivo, reco, cambia, nota=None):
        filas.append({"campo": campo, "vivo": vivo, "recomendado": reco,
                      "cambia": cambia, "nota": nota})

    if "backstop_points" in act:
        vivo_bk = pcfg.get("backstop_points")
        fila("SL / stop de precio fijo (backstop_points)",
             (f"{vivo_bk:g} pts" if vivo_bk
              else (f"SL {sl_atr_vivo}×ATR (legacy)" if sl_atr_vivo
                    else "SL ×ATR heredado")),
             f"{act['backstop_points']:g} pts desde la señal",
             not _num_eq(vivo_bk, act["backstop_points"]),
             nota="el SL×ATR queda IGNORADO mientras el backstop esté activo")
    for k, lado in (("tp_nominal_long", "largos"),
                    ("tp_nominal_short", "cortos")):
        if k in act:
            vivo_tp = pcfg.get(k)
            fila(f"TP nominal {lado} ({k})",
                 (f"{vivo_tp:g}×ATR" if vivo_tp
                  else (f"TP {tp_atr_vivo}×ATR (legacy)" if tp_atr_vivo
                        else "sin TP")),
                 f"{act[k]:g}×ATR (p99 de cierres LuxAlgo)",
                 not _num_eq(vivo_tp, act[k]))
    if "scale_entry" in act:
        vivo_se = pcfg.get("scale_entry") or {}
        reco_se = act["scale_entry"]

        def _se_txt(se):
            if not se:
                return "sin escalera"
            return (f"levels {se.get('levels')} · qty {se.get('quantities')}"
                    f" · max {se.get('max_micro_contracts')}")
        fila("Escalera (levels/quantities/max)", _se_txt(vivo_se),
             _se_txt(reco_se), not _se_igual(vivo_se, reco_se),
             nota="el mode vigente se PRESERVA (NX-11) — aplicar no arma "
                  "la ejecución escalonada")
    if "entry_reserve_timeout_seconds" in act:
        vivo_ca = pcfg.get("entry_reserve_timeout_seconds")
        fila("cancel_after / reserva (s)",
             f"{vivo_ca}s" if vivo_ca else "default (3600s)",
             f"{act['entry_reserve_timeout_seconds']}s",
             not _num_eq(vivo_ca or 3600,
                         act["entry_reserve_timeout_seconds"]),
             nota="⚠ fijar el MISMO valor A MANO en TradersPost "
                  "(Cancel entry after)")
    return filas


def _merge_activacion(pcfg: dict, act: dict) -> dict:
    """Merge de la activación sobre el pcfg vivo. scale_entry preserva el
    mode vigente (NX-11) y el stop_mode; el resto se escribe tal cual.
    NUNCA toca mode/dry_run/traderspost/status (viven fuera del pcfg)."""
    cfg = dict(pcfg or {})
    for k in ("backstop_points", "tp_nominal_long", "tp_nominal_short",
              "entry_reserve_timeout_seconds"):
        if k in act:
            cfg[k] = act[k]
    if "scale_entry" in act:
        prev = cfg.get("scale_entry") or {}
        se = dict(act["scale_entry"])
        prev_mode = prev.get("mode")
        se["mode"] = (prev_mode if prev_mode in ("execute", "live")
                      else "design_only")
        se["stop_mode"] = prev.get("stop_mode", "common_position_stop")
        cfg["scale_entry"] = se
    return cfg


@router.get("/ui/riesgo/aplicar/preview")
async def riesgo_aplicar_preview(strategy: str,
                                 db: AsyncSession = Depends(get_db)
                                 ) -> JSONResponse:
    ctx, err = await _viva_y_reco(db, strategy)
    if err:
        return err
    act = _activacion_json(ctx["reco"])
    prof = ctx["profile"]
    pcfg = (prof.pipeline_config_json or {}) if prof else {}
    sl_vivo = (float(prof.sl_atr_multiplier)
               if prof and prof.sl_atr_multiplier is not None else None)
    tp_vivo = (float(prof.tp_atr_multiplier)
               if prof and prof.tp_atr_multiplier is not None else None)
    return JSONResponse({
        "strategy": strategy.strip(),
        "fecha_estudio": ctx["fecha"],
        "filas": _diff_aplicar(pcfg, act, ctx["fecha"], sl_vivo, tp_vivo),
        "deriva": deriva_estudio(pcfg, act, ctx["fecha"]),
        "avisos": [
            "No toca mode/dry_run/traderspost_enabled/status.",
            "scale_entry conserva su mode vigente (NX-11) — la ejecución "
            "se arma aparte con scripts/set_scale_execution.py.",
            "⚠ cancel_after: fijar el mismo valor A MANO en TradersPost.",
        ],
    })


class AplicarReq(BaseModel):
    strategy: str


@router.post("/ui/riesgo/aplicar")
async def riesgo_aplicar(req: AplicarReq,
                         db: AsyncSession = Depends(get_db)) -> JSONResponse:
    ctx, err = await _viva_y_reco(db, req.strategy)
    if err:
        return err
    strategy = req.strategy.strip()
    act = _activacion_json(ctx["reco"])
    prof = ctx["profile"]
    if prof is None:
        from app.models.strategy_profile import StrategyProfile
        prof = StrategyProfile(strategy_id=strategy)
        db.add(prof)
    before = dict(prof.pipeline_config_json or {})
    cfg = _merge_activacion(before, act)
    prof.pipeline_config_json = cfg
    llaves = [k for k in ("backstop_points", "tp_nominal_long",
                          "tp_nominal_short", "entry_reserve_timeout_seconds",
                          "scale_entry") if k in act]
    from app.services.audit_service import AuditService
    await AuditService().log_strategy_change(
        db, actor="riesgo_aplicar", strategy_id=strategy,
        old_data={k: before.get(k) for k in llaves},
        new_data={k: cfg.get(k) for k in llaves},
        action="APPLY_RIESGO_RECO",
        reason=f"recomendación del estudio {ctx['fecha']} aplicada por el "
               f"operador (preview confirmado)")
    await db.commit()
    return JSONResponse({
        "ok": True, "strategy": strategy, "fecha_estudio": ctx["fecha"],
        "aplicado": {k: cfg.get(k) for k in llaves},
        "deriva": deriva_estudio(cfg, act, ctx["fecha"]),
        "recordatorio": "⚠ cancel_after: fijar el mismo valor a mano en "
                        "TradersPost; la ejecución escalonada se arma con "
                        "scripts/set_scale_execution.py.",
    })


def _aplicar_ctx(res: dict, unidades: dict | None) -> dict | None:
    """R-obs-5 — tarjeta "Configuración a aplicar": el resumen ACCIONABLE
    de lo que el operador configura en TradersPost, desde la recomendación
    VALIDADA (la que se aplica; la protección es para decidir). Cada valor
    en ×ATR + unidad natural del instrumento + $ (Symbol Mapper)."""
    reco = res.get("recomendacion")
    if not reco:
        return None
    ppt = (res.get("meta") or {}).get("usd_por_punto")
    atr_med = (res.get("backstop") or {}).get("atr_mediana_pts")
    gl = (res.get("gestion_lado") or {}).get("recomendacion")

    def unidad_atr(x_atr):
        if x_atr is None or not atr_med:
            return "—"
        return _fmt_unidad(x_atr * atr_med, unidades, ppt)

    filas: list[dict] = []
    if reco.get("backstop"):
        b = reco["backstop"]
        filas.append({
            "campo": "SL / backstop (stop de precio fijo)",
            "valor": (f"{_fmt_unidad(b['pts'], unidades, ppt)} desde la "
                      f"señal (${b['usd_por_micro']:,.0f}/micro)"),
        })
    tp = reco.get("tp_nominal_atr") or {}
    filas.append({
        "campo": "TP nominal (por lado)",
        "valor": (f"L {tp.get('long')}×ATR ≈ {unidad_atr(tp.get('long'))} · "
                  f"S {tp.get('short')}×ATR ≈ {unidad_atr(tp.get('short'))} "
                  f"— por encima de los cierres de LuxAlgo (se toca rara "
                  f"vez)"),
    })
    piernas = sorted((reco.get("escalera") or {}).get("piernas") or [],
                     key=lambda p: p["depth_atr"])
    if piernas:
        filas.append({
            "campo": "Escalera (niveles ATR + cantidad)",
            "valor": " + ".join(
                f"{p['micros']} micros @ {p['depth_atr']:g}×ATR"
                + (f" (≈ {unidad_atr(p['depth_atr'])})"
                   if p["depth_atr"] > 0 else " (a mercado)")
                for p in piernas),
        })
    # bloqueo por lado, sí/no EXPLÍCITO (de la gestión por lado P1b)
    bloquear = gl["lado_malo"] if (gl and gl.get("accion") == "cortar") else None
    filas.append({"campo": "Bloquear largos",
                  "valor": "SÍ — gestión por lado (cortar)"
                  if bloquear == "long" else "no"})
    filas.append({"campo": "Bloquear cortos",
                  "valor": "SÍ — gestión por lado (cortar)"
                  if bloquear == "short" else "no"})
    if reco.get("cancel_after_seconds"):
        filas.append({
            "campo": "cancel_after (entrada límite)",
            "valor": f"{reco['cancel_after_seconds']}s "
                     f"(entry_reserve_timeout_seconds)",
        })
    return {
        "filas": filas,
        "catalogo_incompleto": not (unidades or {}).get("tick_size"),
    }


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


def _estudio_ctx(clave: str, cuenta: float = CUENTA_DEFAULT,
                 unidades: dict | None = None) -> dict | None:
    res = _latest_estudio(clave)
    if res is None:
        return None
    reco = res.get("recomendacion")
    corte = res.get("corte_fills") or {}
    configs = res.get("configs") or []
    heat = _latest_run_file(clave, "heatmap_*.png")
    md = _latest_run_file(clave, "Riesgo_*.md")
    return {
        "fecha": res.get("_fecha"),
        "base": res["linea_base"]["total"],
        "base_pf_oos": res["linea_base"]["out"].get("pf"),
        # C — listado completo + duración media ganador/perdedor (horas)
        "listado_crudo": res.get("listado_crudo"),
        "comparacion": _comparacion(res),
        # B — participación como banner (arriba del veredicto)
        "part_reco": ((reco or {}).get("metricas") or {}
                      ).get("participacion_pct"),
        # A — protección de cuenta (in-sample; selección por la cuenta)
        "proteccion": _proteccion_ctx(res, cuenta, unidades),
        # R-obs-5 — tarjeta "Configuración a aplicar" (unidades del Symbol
        # Mapper — fuente única; aviso si el catálogo está incompleto)
        "aplicar": _aplicar_ctx(res, unidades),
        "reco": reco,
        "sin_reco": None if reco else _motivo_sin_reco(res),
        # P1-2/R-obs-4: backstop legible por instrumento — unidad natural
        # desde el Symbol Mapper (display; el cálculo del stop en L5 no
        # cambia). Sin tick data → $ + aviso de catálogo incompleto.
        "backstop_fmt": (_fmt_unidad(
            reco["backstop"]["pts"], unidades,
            (res.get("meta") or {}).get("usd_por_punto"))
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

@router.get("/ui/riesgo")
async def riesgo_page(strategy: str | None = None) -> RedirectResponse:
    """L7b — Riesgo v1 RETIRADO de la UI (patrón P3, NO destructivo). El motor,
    los datos y los estudios quedan INTACTOS; los helpers y endpoints que reusan
    L5/L7a y las fichas (Puente aplicar/preview, cuenta, heatmap, reporte,
    integrar/calcular, deriva_estudio, _pct_trades_fuera, _merge/_diff) siguen
    vivos en este módulo. La navegación canónica es la sub-pestaña Luxy del
    detalle de Estrategias:
      · `?strategy=X` (clave/id válido) → 302 al detalle de X (sub-pestaña Luxy);
      · sin parámetro → 302 a /ui/strategies.
    Rollback trivial: `git revert` de este commit restaura la página v1 completa
    (la plantilla `riesgo.html` y toda la lógica de contexto siguen en el repo)."""
    if strategy and _KEY_RE.match(strategy):
        return RedirectResponse(f"/ui/strategies/{strategy}", status_code=302)
    return RedirectResponse("/ui/strategies", status_code=302)


# ---------------------------------------------------------------------------
# Subir lista (existente o estrategia NUEVA) → integrar (subproceso awaited)
# ---------------------------------------------------------------------------

def _integrar_cmd(csv_path: Path, codigo: str, activo: str,
                  degradado: bool = False) -> list[str]:
    cmd = [sys.executable, "-m", "scripts.nt_riesgo", "integrar",
           str(csv_path), "--codigo", codigo, "--activo", activo]
    if _stitch():
        cmd.append("--stitch-db")
    if degradado:
        cmd.append("--degradado")
    return cmd


def holc_disponible(instrument: str) -> bool:
    """¿Existe el HOLC 5m del instrumento en NINJATRADER/HOLC (o HOLC_DIR)?
    Fuente ÚNICA: `lab_analyze._holc_dir()` — el mismo directorio que lee el
    motor. Sirve para decidir si el alta puede correr con intrabar o degradada."""
    from scripts.lab_analyze import _holc_dir
    return (_holc_dir() / f"{instrument}_5m.csv").exists()


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


async def integrar_lista(strategy: str, instrument: str, raw: bytes,
                         *, degradado: bool = False,
                         recalc_lab: bool = True) -> dict:
    """Núcleo COMPARTIDO subir-lista → integrar master. Lo usan Riesgo v1
    (`riesgo_upload`) y el alta desde Estrategias (L1) — cero duplicación.

    Escribe el CSV en `TRADES_DIR`, valida con el parser REAL, corre
    `nt_riesgo integrar` en subproceso bajo el lock por estrategia (cuadre al
    dólar bloqueante, sha256, guardia de doble-prefijo, intrabar), actualiza el
    manifest compartido (`_guardar_manifest`) y encadena el recalc del Lab.

    NO resuelve el instrumento — lo pasa el llamador (Riesgo lo detecta del
    nombre del CSV; Estrategias lo toma del activo de la estrategia). `degradado`
    integra SIN HOLC (master cuadrado; estudio pendiente). Devuelve un dict de
    resultado; nunca lanza para errores esperados (los reporta en el dict)."""
    if len(raw) > 20_000_000:
        return {"ok": False, "status": 400, "error": "archivo demasiado grande"}
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
        return {"ok": False, "status": 400,
                "error": "el CSV no parsea como ListaDeOperaciones de LuxAlgo"}

    # integrar (el motor manda: doble-prefijo, cuadre al dólar, TZ) —
    # SERIALIZADO por estrategia (P1-4): el lock cubre subproceso + manifest.
    clave = clave_de(strategy, instrument)
    async with _lock_integrar(strategy):
        rc, tail = await _run_motor(
            _integrar_cmd(dest, derive_codigo(strategy, instrument),
                          instrument, degradado=degradado))
        if rc != 0:
            dest.unlink(missing_ok=True)
            return {"ok": False, "status": 400, "error": "integrar falló",
                    "detalle": tail.strip()[-800:]}

        # Manifest: actualizar CSV (existente) o ALTA confirmada (nueva) —
        # el `lab_manifest propose --confirm` hecho por UI.
        manifest = routes_lab.load_manifest()      # releer bajo el lock
        entry = manifest.get(strategy)
        rel = dest.as_posix()
        manifest[strategy] = {**(entry or {"candidates": None}),
                              "instrument": instrument, "csv": rel,
                              "confirmed": True}
        _guardar_manifest(manifest)
        nueva = entry is None

    # LAB-1 — encadenar el recalc de la caché del Lab para esta estrategia
    # (2º plano; opt-out). El manifest ya apunta al CSV recién integrado, así
    # que el mismo listado alimenta las dos pestañas. Nunca recomputa aquí.
    lab_recalc = (routes_lab._enqueue_recalc(strategy) if recalc_lab
                  else False)

    motor = _motor_manifest(clave) or {}
    return {
        "ok": True, "clave": clave, "strategy": strategy, "nueva": nueva,
        "degradado": bool((motor.get("holc") or {}).get("degradado")
                          or degradado),
        "n_trades": (motor.get("trades") or {}).get("n", len(trades)),
        "integrado": motor.get("integrado"),
        "lab_recalc": ("running" if lab_recalc
                       else ("busy" if recalc_lab else "skipped")),
    }


@router.post("/ui/riesgo/upload")
async def riesgo_upload(strategy: str = Form(...),
                        file: UploadFile = File(...),
                        recalc_lab: bool = Form(True)) -> JSONResponse:
    """Sube la lista de operaciones y corre `integrar` (cuadre al dólar
    bloqueante, guardia de doble-prefijo — los errores del motor se muestran
    tal cual). Estrategia fuera del manifest = ALTA NUEVA: símbolo detectado
    del nombre original del CSV, mapeo confirmado por UI.

    LAB-1: un solo upload deja las DOS pestañas frescas — además de integrar el
    master del Motor, encadena el recálculo de la caché del Lab para esta
    estrategia (2º plano, opt-out con recalc_lab=false). El núcleo vive en
    `integrar_lista` (compartido con el alta de Estrategias)."""
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
    result = await integrar_lista(strategy, instrument, raw,
                                  recalc_lab=recalc_lab)
    if not result["ok"]:
        payload = {k: v for k, v in result.items()
                   if k in ("error", "detalle")}
        return JSONResponse(payload, status_code=result["status"])
    result["hint"] = "dale Calcular para correr el estudio"
    return JSONResponse(result)


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
    motor = _motor_manifest(clave)
    if motor is None:
        return JSONResponse({"error": "sin listado integrado — sube el "
                                      "CSV primero"}, status_code=409)
    # L1.1 — fail-honest: `calcular` necesita el intrabar (HOLC). Un master
    # integrado en modo DEGRADADO no lo tiene, así que el estudio no puede
    # correr (hoy reventaría sucio en el motor). La semántica de estudio sobre
    # degradado se define en L2; hasta entonces, freno claro.
    if (motor.get("degradado")
            or (motor.get("holc") or {}).get("degradado")):
        return JSONResponse(
            {"error": "master degradado (sin HOLC/intrabar) — provee el HOLC "
                      "y reintegra antes de calcular"}, status_code=409)
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
# v2-A: cuenta editable ($, persiste) — la protección recomputa al cambiar
# ---------------------------------------------------------------------------

class CuentaReq(BaseModel):
    cuenta_usd: float
    strategy: str | None = None


@router.post("/ui/riesgo/cuenta")
async def riesgo_cuenta(req: CuentaReq) -> JSONResponse:
    """R-obs-2: con `strategy` la cuenta se guarda POR ESTRATEGIA
    (MotorRiesgo/<clave>/cuenta.json); sin ella, el global (retrocompat)."""
    v = req.cuenta_usd
    if not (100.0 <= v <= 100_000_000.0):
        return JSONResponse(
            {"error": "cuenta fuera de rango ($100 – $100,000,000)"},
            status_code=400)
    destino = MOTOR_DIR
    if req.strategy:
        clave = _resolver_clave(req.strategy)
        if clave is None:
            return JSONResponse({"error": "estrategia fuera del manifest"},
                                status_code=400)
        destino = MOTOR_DIR / clave
    destino.mkdir(parents=True, exist_ok=True)
    (destino / "cuenta.json").write_text(
        json.dumps({"cuenta_usd": v}), encoding="utf-8")
    return JSONResponse({"ok": True, "cuenta_usd": v,
                         "ambito": (f"estrategia {req.strategy}"
                                    if req.strategy else "global")})


# ---------------------------------------------------------------------------
# v2-D: gestión de datos — renombrar/eliminar la estrategia del estudio y
# eliminar/reemplazar el listado (.csv). SOLO toca el estudio (manifest del
# Lab + carpeta MotorRiesgo + CSV subido por la pestaña) — la estrategia
# VIVA de la DB jamás se toca desde aquí.
# ---------------------------------------------------------------------------

def _borrar_datos(clave: str, entry: dict) -> dict:
    """Borra la carpeta del motor y el CSV SUBIDO por la pestaña (solo
    upload_* — jamás un export original del operador)."""
    carpeta = MOTOR_DIR / clave
    motor_borrado = carpeta.exists()
    if motor_borrado:
        shutil.rmtree(carpeta, ignore_errors=True)
    csv_p = Path(entry.get("csv") or "")
    csv_borrado = csv_p.name.startswith("upload_") and csv_p.exists()
    if csv_borrado:
        csv_p.unlink(missing_ok=True)
    return {"motor_borrado": motor_borrado, "csv_borrado": csv_borrado}


class RenombrarReq(BaseModel):
    strategy: str
    nuevo_id: str


@router.post("/ui/riesgo/estrategia/renombrar")
async def riesgo_renombrar(req: RenombrarReq) -> JSONResponse:
    de = (req.strategy or "").strip()
    a = (req.nuevo_id or "").strip()
    if not (_KEY_RE.match(de) and _KEY_RE.match(a)):
        return JSONResponse({"error": "id inválido (letras/números/_/-, "
                                      "máx 64)"}, status_code=400)
    if de == a:
        return JSONResponse({"error": "el id nuevo es igual al actual"},
                            status_code=400)
    async with _lock_integrar(de):
        manifest = routes_lab.load_manifest()
        entry = manifest.get(de)
        if entry is None:
            return JSONResponse({"error": "estrategia fuera del manifest"},
                                status_code=400)
        if a in manifest:
            return JSONResponse({"error": f"ya existe {a} en el manifest"},
                                status_code=409)
        instrument = entry["instrument"]
        clave_old = clave_de(de, instrument)
        clave_new = clave_de(a, instrument)
        if (JOBS.get(clave_old) or {}).get("status") == "running":
            return JSONResponse({"error": "hay un cálculo corriendo — "
                                          "espera a que termine"},
                                status_code=409)
        if clave_new != clave_old and (MOTOR_DIR / clave_old).exists():
            if (MOTOR_DIR / clave_new).exists():
                return JSONResponse(
                    {"error": f"la carpeta destino {clave_new} ya existe"},
                    status_code=409)
            (MOTOR_DIR / clave_old).rename(MOTOR_DIR / clave_new)
        manifest[a] = manifest.pop(de)
        _guardar_manifest(manifest)
        JOBS.pop(clave_old, None)
        # LAB-2 — mover también la caché del Lab (llaveada por strategy_id) si
        # existe; si no, no-op. Que el renombre no la deje huérfana.
        lab_cache_movida = routes_lab.rename_lab_cache(de, a)
    return JSONResponse({"ok": True, "strategy": a, "clave": clave_new,
                         "lab_cache_movida": lab_cache_movida,
                         "nota": "solo el ESTUDIO se renombró — la "
                                 "estrategia viva de la DB no se toca"})


@router.delete("/ui/riesgo/estrategia")
async def riesgo_estrategia_eliminar(strategy: str) -> JSONResponse:
    strategy = (strategy or "").strip()
    if not _KEY_RE.match(strategy):
        return JSONResponse({"error": "strategy_id inválido"},
                            status_code=400)
    async with _lock_integrar(strategy):
        manifest = routes_lab.load_manifest()
        entry = manifest.get(strategy)
        if entry is None:
            return JSONResponse({"error": "estrategia fuera del manifest"},
                                status_code=400)
        clave = clave_de(strategy, entry["instrument"])
        if (JOBS.get(clave) or {}).get("status") == "running":
            return JSONResponse({"error": "hay un cálculo corriendo — "
                                          "espera a que termine"},
                                status_code=409)
        borrado = _borrar_datos(clave, entry)
        manifest.pop(strategy)
        _guardar_manifest(manifest)
        JOBS.pop(clave, None)
        # LAB-2 — borrar también la caché del Lab (REPORTES/lab_features_<key>)
        # para no dejarla huérfana. El key del Lab = strategy_id del manifest.
        lab_cache_borrada = routes_lab.delete_lab_cache(strategy)
    return JSONResponse({"ok": True, "eliminada": strategy,
                         "lab_cache_borrada": lab_cache_borrada, **borrado})


@router.delete("/ui/riesgo/datos")
async def riesgo_datos_eliminar(strategy: str) -> JSONResponse:
    """Elimina el LISTADO integrado (.csv subido + carpeta del motor con
    master/runs/snapshots) pero CONSERVA la estrategia en el manifest —
    queda lista para subir/reemplazar con un export nuevo."""
    strategy = (strategy or "").strip()
    if not _KEY_RE.match(strategy):
        return JSONResponse({"error": "strategy_id inválido"},
                            status_code=400)
    async with _lock_integrar(strategy):
        manifest = routes_lab.load_manifest()
        entry = manifest.get(strategy)
        if entry is None:
            return JSONResponse({"error": "estrategia fuera del manifest"},
                                status_code=400)
        clave = clave_de(strategy, entry["instrument"])
        if (JOBS.get(clave) or {}).get("status") == "running":
            return JSONResponse({"error": "hay un cálculo corriendo — "
                                          "espera a que termine"},
                                status_code=409)
        borrado = _borrar_datos(clave, entry)
        JOBS.pop(clave, None)
    return JSONResponse({"ok": True, "strategy": strategy, **borrado})


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
