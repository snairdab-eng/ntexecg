"""Checkpoint #0 — Auditoría READ-ONLY del bracket post-aplicación.

Pregunta que responde: ¿las entradas APROBADAS *después* de aplicar la
recomendación del estudio (AuditLog APPLY_RIESGO_RECO) por estrategia
salieron con el bracket NUEVO?

  Bracket NUEVO esperado por estrategia:
    - stopLoss.stopPrice = señal ∓ backstop_points  (precio FIJO, decimales
      calcando la entrada; long: entrada − bp, short: entrada + bp).
    - takeProfit.limitPrice = entrada ± ATR·tp_nominal_<lado>  (nominal ×ATR).
  Bracket VIEJO (lo que NO debe aparecer post-aplicación):
    - stop por ATR (sl_mode == "atr"), o
    - TP legacy k×ATR (tp_mode == "legacy_atr"), o
    - sin TP cuando el nominal está configurado.

Para cada entrada aprobada post-aplicación compara lo REALMENTE enviado a
TradersPost (WebhookDelivery.payload_json) contra el bracket recomputado con
la config efectiva (ConfigResolver + SLTPCalculator) y contra la aritmética
exacta del bracket nuevo, y emite PASS/FAIL.

INVARIANTES: solo lectura. No escribe, no despacha, no commitea. No abre
transacciones de escritura. Ante un FAIL no arregla nada: captura el payload
y el detalle para el arquitecto.

Uso:
    .venv/Scripts/python.exe scripts/audit_bracket_post_apply.py
    (apunta DATABASE_URL a la BD que corresponda auditar)
"""
from __future__ import annotations

import asyncio
import json
import sys
from decimal import Decimal

# La consola de Windows (cp1252) no encodea flechas/símbolos — forzar UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.audit_log import AuditLog
from app.models.decision import StrategyDecision
from app.models.normalized_signal import NormalizedSignal
from app.models.webhook_delivery import WebhookDelivery
from app.services.config_resolver import ConfigResolver
from app.services.sl_tp_calculator import (
    SLTPCalculator,
    backstop_config,
    tp_nominal_config,
)

# Tolerancia de comparación de precios cuando el catálogo no trae tick_size.
# En general se usa MEDIA TICK del instrumento (ver _tol): el broker redondea
# el bracket a tick, así que una diferencia sub-tick no es discrepancia real.
EPS = 1e-6

# Roles/acciones que representan una SALIDA (cierran posición, NO abren): por
# contrato (payload_builder) NUNCA llevan bracket. No se auditan como entrada
# — se cuentan aparte y jamás son FAIL. La auditoría del bracket es solo para
# ENTRADAS aprobadas.
_EXIT_ROLES = {"exit_long", "exit_short"}
_EXIT_ACTIONS = {"exit", "flatten", "close"}


def _is_exit(action, signal_role) -> bool:
    return (action in _EXIT_ACTIONS) or ((signal_role or "") in _EXIT_ROLES)


def _tol(config: dict) -> float:
    """Media tick del instrumento (Symbol Mapper tick_size). El broker redondea
    el bracket a tick, luego una diferencia sub-tick no es discrepancia real.
    Sin tick_size en el catálogo → cae a EPS absoluto."""
    ts = config.get("tick_size")
    if ts:
        return max(EPS, float(ts) / 2.0)
    return EPS


def _decimals_of(x) -> int:
    """Nº de decimales con que viene expresada la ENTRADA (para 'calcar' el
    stop de precio fijo). Trabaja sobre el Decimal exacto del Numeric(18,6)."""
    d = Decimal(str(x)).normalize()
    exp = d.as_tuple().exponent
    return max(0, -exp) if isinstance(exp, int) else 0


def _f(x):
    return None if x is None else float(x)


def _eq(a, b, tol=EPS) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= tol


async def _apply_events(db) -> dict[str, dict]:
    """{strategy_id: {first, last, new_value_json}} de APPLY_RIESGO_RECO.

    first = primera vez que el bracket nuevo entró en vivo (frontera para
    'post-aplicación'). last = última aplicación (config vigente esperada).
    """
    rows = (
        await db.execute(
            select(AuditLog)
            .where(AuditLog.action == "APPLY_RIESGO_RECO")
            .order_by(AuditLog.created_at)
        )
    ).scalars().all()
    out: dict[str, dict] = {}
    for r in rows:
        sid = r.object_id or "<sin object_id>"
        rec = out.setdefault(
            sid, {"first": r.created_at, "last": r.created_at, "new_value_json": {}}
        )
        rec["last"] = r.created_at
        if r.new_value_json:
            rec["new_value_json"] = r.new_value_json
    return out


async def _approved_after(db, strategy_id: str, cutoff):
    """Decisiones APPROVE de la estrategia posteriores a la aplicación,
    con su señal normalizada. Ordenadas por fecha de decisión."""
    q = (
        select(StrategyDecision, NormalizedSignal)
        .join(
            NormalizedSignal,
            NormalizedSignal.id == StrategyDecision.normalized_signal_id,
        )
        .where(
            StrategyDecision.strategy_id == strategy_id,
            StrategyDecision.outcome == "APPROVE",
            StrategyDecision.decided_at > cutoff,
        )
        .order_by(StrategyDecision.decided_at)
    )
    return (await db.execute(q)).all()


async def _deliveries(db, decision_id):
    q = (
        select(WebhookDelivery)
        .where(WebhookDelivery.decision_id == decision_id)
        .order_by(WebhookDelivery.created_at)
    )
    return (await db.execute(q)).scalars().all()


def _sent_bracket(payload: dict):
    """(stopPrice, limitPrice, action) tal como se envió a TradersPost."""
    sl = (payload or {}).get("stopLoss") or {}
    tp = (payload or {}).get("takeProfit") or {}
    return sl.get("stopPrice"), tp.get("limitPrice"), (payload or {}).get("action")


async def audit(db) -> None:
    resolver = ConfigResolver()
    calc = SLTPCalculator()

    applies = await _apply_events(db)

    print("=" * 100)
    print("CHECKPOINT #0 — AUDITORÍA READ-ONLY: bracket de entradas aprobadas "
          "POST-aplicación")
    print("=" * 100)
    print(f"\nEventos APPLY_RIESGO_RECO en AuditLog: {len(applies)}")
    for sid, ev in applies.items():
        nv = ev["new_value_json"] or {}
        print(f"  · {sid}: primera={ev['first']}  última={ev['last']}")
        print(f"      bracket aplicado: backstop_points={nv.get('backstop_points')} "
              f"tp_nominal_long={nv.get('tp_nominal_long')} "
              f"tp_nominal_short={nv.get('tp_nominal_short')}")

    if not applies:
        print("\n>>> No hay NINGÚN APPLY_RIESGO_RECO en esta base. No se aplicó "
              "la recomendación en este entorno (o la BD no es la que registró "
              "el Puente). Nada que auditar aquí.")
        _global_status(0, 0, 0, applies)
        return

    rows_summary: list[dict] = []
    exit_rows: list[dict] = []
    total_post = 0
    total_pass = 0
    total_old = 0
    total_exit = 0

    for sid, ev in applies.items():
        cutoff = ev["first"]
        decisions = await _approved_after(db, sid, cutoff)
        for decision, signal in decisions:
            # SALIDA: cierra posición, no abre — por contrato NO lleva bracket.
            # Se cuenta aparte ("exits (sin bracket — correcto)"), nunca se
            # evalúa como entrada, nunca es FAIL.
            if _is_exit(signal.action, signal.signal_role):
                total_exit += 1
                exit_rows.append({
                    "sid": sid, "when": decision.decided_at,
                    "action": signal.action, "role": signal.signal_role,
                })
                continue

            total_post += 1
            entry = _f(signal.price)
            side = "LONG" if signal.action == "buy" else (
                "SHORT" if signal.action == "sell" else signal.action)
            is_long = signal.action == "buy"

            lvl5 = ((decision.pipeline_execution_json or {}).get("level_5") or {})
            sl_mode = lvl5.get("sl_mode")
            tp_mode = lvl5.get("tp_mode")
            # ATR redondeado a Numeric(18,6) en decision.atr_value. Recomputar el
            # TP con él difiere en el 5º decimal del ATR de plena precisión que
            # usó el despacho (viaja en payload.extras.atr_value) → se prefiere
            # ese por-delivery más abajo. Este queda como fallback.
            atr = _f(decision.atr_value)

            # Config efectiva y tolerancia por tick del instrumento.
            config = await resolver.resolve(db, sid, signal.ticker_received)
            tol = _tol(config)

            # Aritmética EXACTA del bracket nuevo (independiente del calc).
            bp = backstop_config(config)
            nominal = tp_nominal_config(config, is_long)
            exp_stop_fixed = None
            if bp is not None and entry is not None:
                raw = entry - bp if is_long else entry + bp
                exp_stop_fixed = round(raw, _decimals_of(signal.price))
            # Fallback (sin delivery): TP con el ATR redondeado de la decisión.
            exp_tp_nominal = None
            if nominal is not None and atr and entry is not None:
                exp_tp_nominal = (entry + atr * nominal if is_long
                                  else entry - atr * nominal)

            deliveries = await _deliveries(db, decision.id)
            if not deliveries:
                rows_summary.append({
                    "sid": sid, "when": decision.decided_at, "side": side,
                    "entry": entry, "sent_sl": None, "exp_sl": exp_stop_fixed,
                    "sent_tp": None, "exp_tp": exp_tp_nominal,
                    "sl_mode": sl_mode, "tp_mode": tp_mode,
                    "verdict": "FAIL", "old": False,
                    "notes": "APPROVE sin WebhookDelivery (no se registró envío)",
                    "payload": None,
                })
                continue

            for d in deliveries:
                sent_sl, sent_tp, act = _sent_bracket(d.payload_json)

                # ATR REALMENTE DESPACHADO (plena precisión, en extras). El TP
                # (y el stop cuando aplique) se recomputan con ESTE, no con el
                # redondeado de la decisión, para no marcar una diferencia
                # sub-tick del 5º decimal como discrepancia.
                extras = (d.payload_json or {}).get("extras") or {}
                dispatch_atr = (float(extras["atr_value"])
                                if extras.get("atr_value") is not None else atr)
                recompute = await calc.calculate(signal, dispatch_atr, entry, config)
                exp_sl_calc = recompute.get("sl_price")
                if nominal is not None and dispatch_atr and entry is not None:
                    exp_tp_nominal = (entry + dispatch_atr * nominal if is_long
                                      else entry - dispatch_atr * nominal)

                # ¿Salió con bracket VIEJO?
                old_flags = []
                if sl_mode == "atr":
                    old_flags.append("stop por ATR (sl_mode=atr)")
                if tp_mode == "legacy_atr":
                    old_flags.append("TP legacy k×ATR (tp_mode=legacy_atr)")
                if nominal is not None and sent_tp is None:
                    old_flags.append("sin TP pese a nominal configurado")
                is_old = bool(old_flags)

                checks = []
                ok = True
                # Stop enviado == señal ∓ backstop exacto (decimales de la entrada).
                if exp_stop_fixed is not None:
                    if not _eq(sent_sl, exp_stop_fixed, tol):
                        ok = False
                        checks.append(
                            f"stop enviado {sent_sl} != fijo esperado {exp_stop_fixed}")
                    if sl_mode != "backstop_fixed":
                        ok = False
                        checks.append(f"sl_mode={sl_mode} (esperado backstop_fixed)")
                else:
                    ok = False
                    checks.append("estrategia sin backstop_points en config efectiva "
                                  "(no puede haber stop fijo)")
                # Coherencia con el recompute canónico del calculador.
                if not _eq(sent_sl, exp_sl_calc, tol):
                    ok = False
                    checks.append(
                        f"stop enviado {sent_sl} != recompute {exp_sl_calc}")
                # TP enviado == nominal ×ATR del lado.
                if exp_tp_nominal is not None:
                    if not _eq(sent_tp, exp_tp_nominal, tol):
                        ok = False
                        checks.append(
                            f"TP enviado {sent_tp} != nominal esperado "
                            f"{exp_tp_nominal}")
                    if tp_mode != "nominal_atr":
                        ok = False
                        checks.append(f"tp_mode={tp_mode} (esperado nominal_atr)")
                elif sent_tp is not None:
                    checks.append(
                        f"TP enviado {sent_tp} sin nominal esperable "
                        f"(tp_mode={tp_mode}) — revisar")

                if is_old:
                    ok = False

                verdict = "PASS" if ok else "FAIL"
                if ok:
                    total_pass += 1
                if is_old:
                    total_old += 1

                rows_summary.append({
                    "sid": sid, "when": decision.decided_at, "side": side,
                    "entry": entry, "sent_sl": sent_sl, "exp_sl": exp_stop_fixed,
                    "sent_tp": sent_tp, "exp_tp": exp_tp_nominal,
                    "sl_mode": sl_mode, "tp_mode": tp_mode,
                    "verdict": verdict, "old": is_old,
                    "dest": d.destination, "status": d.status,
                    "notes": "; ".join(old_flags + checks) or "ok",
                    "payload": d.payload_json if not ok else None,
                })

    _print_table(rows_summary, exit_rows)
    _global_status(total_post, total_pass, total_old, total_exit,
                   applies, rows_summary)


def _print_table(rows: list[dict], exit_rows: list[dict] | None = None) -> None:
    print("\n" + "-" * 100)
    print("TABLA RESUMEN — ENTRADAS (auditadas por bracket)")
    print("-" * 100)
    if not rows:
        print("(sin filas — ninguna entrada aprobada post-aplicación)")
    for r in rows:
        print(f"\n[{r['verdict']}]{'  <BRACKET VIEJO>' if r['old'] else ''}  "
              f"estrategia={r['sid']}  {r['side']}  {r['when']}")
        print(f"    entrada        : {r['entry']}")
        print(f"    stop enviado   : {r['sent_sl']}   esperado(fijo): {r['exp_sl']}"
              f"   sl_mode={r['sl_mode']}")
        print(f"    TP   enviado   : {r['sent_tp']}   esperado(nom) : {r['exp_tp']}"
              f"   tp_mode={r['tp_mode']}")
        if r.get("dest"):
            print(f"    destino/status : {r['dest']} / {r['status']}")
        print(f"    detalle        : {r['notes']}")
        if r.get("payload") is not None:
            print("    PAYLOAD ENVIADO (capturado para el arquitecto):")
            print("    " + json.dumps(r["payload"], default=str,
                                      ensure_ascii=False))

    # Salidas: cierran posición, no abren — sin bracket por contrato. Se
    # listan aparte, NO se auditan como entrada (nunca FAIL).
    exit_rows = exit_rows or []
    print("\n" + "-" * 100)
    print(f"SALIDAS post-aplicación (sin bracket — correcto): {len(exit_rows)}")
    print("-" * 100)
    if not exit_rows:
        print("(ninguna salida aprobada post-aplicación)")
    for r in exit_rows:
        print(f"    [EXIT — OK sin bracket]  estrategia={r['sid']}  "
              f"action={r['action']}  role={r['role']}  {r['when']}")


def _global_status(total_post, total_pass, total_old, total_exit,
                   applies, rows=None) -> None:
    print("\n" + "=" * 100)
    print("ESTADO GLOBAL")
    print("=" * 100)
    if not applies:
        print("APPLY_RIESGO_RECO: 0 → no hay aplicación registrada en este "
              "entorno; auditoría sin objeto aquí.")
        return
    if total_post == 0:
        print("Aún NO hay ninguna ENTRADA aprobada (APPROVE) posterior a la "
              "aplicación de su estrategia. Nada que auditar por bracket "
              "todavía: el bracket nuevo no ha llegado a producir una entrada.")
        print(f"Salidas aprobadas post-aplicación  : {total_exit}  "
              f"(sin bracket — correcto, no se auditan)")
        return
    n_fail = sum(1 for r in (rows or []) if r["verdict"] == "FAIL")
    print(f"Entradas aprobadas post-aplicación : {total_post}")
    print(f"  PASS (bracket nuevo correcto)     : {total_pass}")
    print(f"  FAIL                              : {n_fail}")
    print(f"  con BRACKET VIEJO detectado       : {total_old}")
    print(f"Salidas aprobadas post-aplicación  : {total_exit}  "
          f"(sin bracket — correcto, contadas aparte, NO son FAIL)")
    if total_old:
        print("  >>> ALERTA: hay entradas con bracket VIEJO post-aplicación. "
              "Payloads capturados arriba para el arquitecto. NO se corrigió "
              "nada (solo lectura).")


async def main() -> None:
    async with AsyncSessionLocal() as db:
        await audit(db)


if __name__ == "__main__":
    asyncio.run(main())
