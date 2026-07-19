"""RA-2b SUB-PASO 5 — RearmJob: la plomería sobre el cerebro ya blindado.

leer estado (sub-paso 2) → inferir (sub-paso 3) → decidir (sub-paso 4) →
ejecutar. El scheduler (app/core/scheduler.py:RearmJob) llama `rearm_sweep`
cada 60 s; supuesto 1-worker HEREDADO de ExitManagerJob (P1-2 del diseño:
sin lock distribuido — la persistencia hace el job idempotente ante RESTART,
no ante concurrencia de workers).

INVARIANTES:
  · Por posición, SU PROPIA transacción (sesión propia, commit/rollback
    aislado): una excepción hace rollback de ESA posición, se registra
    REARM_SKIP{error} en una sesión FRESCA y el barrido CONTINÚA — el job
    jamás muere ni deja estado a medias.
  · El job SOLO escribe risk_plan_json["rearm"] (PositionService.
    set_rearm_state) — jamás state/direction/quantity (dominio de
    position_service; invariante (d) del sub-paso 2).
  · `rearm.enabled` se RE-VERIFICA de la config efectiva EN CADA BARRIDO:
    apagado a media vida ⇒ no se re-arma más (REARM_SKIP{disabled}).
  · Default OFF absoluto: sin estado sembrado (plan sin "rearm") la posición
    ni entra al camino auditado.

SEMÁNTICA EN DRY_RUN (decisión, justificada): el ciclo AVANZA con delivery
DRY_RUN — coherencia del mundo paper: el estimado de posición también avanza
en dry-run, y el modelo sin-solape/horizonte del estudio debe observarse EN
PAPER exactamente como en vivo (la demo valida el timing; si no avanzara, el
job "re-enviaría" cada 60 s y el ciclo perdería sentido). Solo un FAILED
TOTAL (ningún destino SENT/DRY_RUN) no avanza: nada salió, el re-envío se
reintenta honesto al siguiente barrido — sin riesgo de doble orden porque
ninguna orden quedó viva.

AUDIT (diseño §6 + E2): REARM_LEG (re-envío) · REARM_KILL (muerte, con la
regla R-RA que cortó) · REARM_SKIP (fail-closed, con motivo) ·
REARM_ASSUMED (R-RA2 en ventana viva — verbo PROPIO: la pierna NO está
muerta, está asumida llena y cuenta exposición; llamarla KILL mentiría en la
reconstrucción de la demo).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select

from app.models.position_state import PositionState
from app.services.rearm import (
    _a_et_naive,
    avanzar_ciclo,
    decidir_pierna,
    leer_estado,
    marcar_assumed_filled,
    marcar_muerta,
    normalize_rearm,
    obtener_inferencia,
    rearm_config,
    rearm_enabled,
    ttl_coherente,
)

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _audit(db, pos, action: str, payload: dict) -> None:
    from app.services.audit_service import AuditService
    await AuditService().log(
        db, actor="rearm_job", action=action, object_type="PositionState",
        object_id=f"{pos.account_id}:{pos.symbol}", new_value=payload)


async def _decision_sintetica(db, pos, side: str, now: datetime):
    """Cadena RawSignal→NormalizedSignal→StrategyDecision del re-envío (una
    por posición y barrido; WebhookDelivery.decision_id es NOT NULL) — el
    MISMO patrón de forced_exit (la otra fuente autónoma de despacho)."""
    from app.models.decision import StrategyDecision
    from app.models.normalized_signal import NormalizedSignal
    from app.models.raw_signal import RawSignal

    action = "buy" if side == "long" else "sell"
    raw = RawSignal(source="rearm_job", strategy_id=pos.strategy_id,
                    ticker_received=pos.symbol, action=action, sentiment=side,
                    quantity_raw="0", payload_json={"rearm": True},
                    token_valid=True)
    db.add(raw)
    await db.flush()
    norm = NormalizedSignal(
        raw_signal_id=raw.id, source="rearm_job",
        strategy_id=pos.strategy_id, ticker_received=pos.symbol,
        mapped_symbol=pos.symbol, action=action, sentiment=side, quantity=0,
        signal_ts=now,
        signal_role="entry_long" if side == "long" else "entry_short",
        dedupe_key=f"rearm:{uuid.uuid4().hex}", status="processed")
    db.add(norm)
    await db.flush()
    decision = StrategyDecision(
        normalized_signal_id=norm.id, strategy_id=pos.strategy_id,
        outcome="REARM", reason_detail="rearm_job:reenvio",
        pipeline_execution_json={"rearm": True})
    db.add(decision)
    await db.flush()
    return decision


def _failed_ambiguo(result) -> bool:
    """E3 — ¿este FAILED es AMBIGUO (la orden PUDO quedar viva en el broker)?
    Inequívoco = hubo respuesta HTTP (status code presente), rechazo de
    conexión claro (ConnectError: el canal nunca se estableció) o URL ausente
    (nada se intentó). El flag por-intento del cliente manda (un timeout en
    CUALQUIER intento contamina aunque el último recibiera respuesta); el
    resto es defensa ante resultados sin el campo (fakes/versiones viejas)."""
    if getattr(result, "any_ambiguous_attempt", False):
        return True
    if result.response_status_code is not None:
        return False
    return result.error_message not in ("no_webhook_url_configured",
                                        "ConnectError")


async def _reenviar_pierna(db, pos, config, estado, leg, settings,
                           now: datetime, decision_box: dict) -> str:
    """Ejecuta el re-envío de UNA pierna por TODOS los destinos con el MISMO
    gate por capas que una entrada. Devuelve: "ok" (≥1 SENT/DRY_RUN),
    "fallido" (todos FAILED, TODOS inequívocos — reintento legítimo),
    "ambiguo" (todos FAILED y ≥1 ambiguo — E3: la pierna se MATA, jamás se
    re-envía sobre ambigüedad) o "sin_qty" (el perfil vigente ya no asigna
    contratos a la pierna en el destino BASE)."""
    from app.api.webhooks_luxalgo import resolve_effective_dry_run
    from app.models.webhook_delivery import WebhookDelivery
    from app.services import dispatch_profiles as dprof
    from app.services.payload_builder import PayloadBuilder
    from app.services.traderspost_client import TradersPostClient

    side = leg["side"]
    ciclo = int(leg["cycle_n"]) + 1
    base_id = str(pos.entry_signal_id or f"{pos.account_id}:{pos.symbol}")
    client_id = f"{base_id}-r{ciclo}"          # correlación diseño §5
    builder = PayloadBuilder()
    client = TradersPostClient(settings)
    role = "entry_long" if side == "long" else "entry_short"
    any_ok = any_failed = any_ambiguo = False
    for di, dest in enumerate(dprof.resolve_destinations(config)):
        dest_config = dprof.make_dest_config(config, dest)
        payload = builder.build_rearm_leg(
            symbol=pos.symbol, side=side, leg_state=leg, config=dest_config,
            sl_price=estado.get("sl_price"), tp_price=estado.get("tp_price"),
            strategy_id=pos.strategy_id, signal_id=base_id,
            client_id=client_id, cycle_n=ciclo)
        if payload is None:
            if di == 0:
                return "sin_qty"           # el BASE manda: pierna sin destino
            continue                       # un perfil sin qty solo se salta
        # MISMO gate que una entrada: kill-switch por capas + dry_run por
        # destino (dry ⇒ delivery DRY_RUN, nada llega al broker).
        dry_run = resolve_effective_dry_run(settings, dest_config)
        result = await client.send(
            dest["webhook_url"] or "", payload, signal_role=role,
            dry_run=dry_run, signal_ts=now,
            backoff_seconds=config.get("retry_backoff_seconds"))
        if decision_box.get("d") is None:
            decision_box["d"] = await _decision_sintetica(db, pos, side, now)
        db.add(WebhookDelivery(
            decision_id=decision_box["d"].id, strategy_id=pos.strategy_id,
            destination=dprof.delivery_tag(dest["name"]),
            url_masked=result.url_masked, payload_json=result.payload_json,
            response_status_code=result.response_status_code,
            response_body=result.response_body, status=result.status,
            attempts=result.attempts, latency_ms=result.latency_ms,
            error_message=result.error_message,
            sent_at=_utcnow() if result.status == "SENT" else None))
        if result.status in ("SENT", "DRY_RUN"):
            any_ok = True
        if result.status == "FAILED":
            any_failed = True
            if _failed_ambiguo(result):
                any_ambiguo = True         # E3
    if any_ok:
        return "ok"
    if any_failed and any_ambiguo:
        return "ambiguo"                   # E3 — matar, jamás reintentar
    return "fallido" if any_failed else "sin_qty"


async def procesar_posicion(db, pos_id, settings, market_data,
                            now: datetime | None = None) -> str | None:
    """Una posición, UNA transacción (el caller comitea/rollbackea). Devuelve
    una etiqueta corta del resultado (para logs/tests) o None si no aplica."""
    from app.services.config_resolver import ConfigResolver
    from app.services.position_service import PositionService
    from app.services.repositories import get_strategy_by_id
    from app.services.symbol_mapper import SymbolMapper

    now = now or _utcnow()
    pos = await db.get(PositionState, pos_id)
    if pos is None:
        return None
    # El ESTADO de la posición no filtra aquí: lo juzga el MOTOR (R-RA5 mata
    # re-armados en EXITING/FLAT/REVERSING/UNKNOWN/LOCKED — diseño §4). El
    # filtro barato es el plan: sin "rearm" sembrado no hay nada que razonar.
    plan = dict(pos.risk_plan_json or {})
    if "rearm" not in plan:
        return None                        # sin estado sembrado: caso normal
    estado = leer_estado(plan)
    working_idx = ([i for i, l in enumerate(estado["legs"])
                    if l["state"] == "working"] if estado else [])
    if estado is not None and not working_idx:
        return None                        # nada vivo que razonar (silencioso)

    strategy = await get_strategy_by_id(db, pos.strategy_id) \
        if pos.strategy_id else None
    if strategy is None:
        await _audit(db, pos, "REARM_SKIP", {"motivo": "sin_estrategia"})
        return "skip:sin_estrategia"
    config = await ConfigResolver().resolve(db, pos.strategy_id,
                                            strategy.asset_symbol)
    # RE-VERIFICADO en cada barrido: apagado a media vida ⇒ no re-armar más.
    if not rearm_enabled(config):
        await _audit(db, pos, "REARM_SKIP", {"motivo": "disabled"})
        return "skip:disabled"
    # E1 defensa en profundidad: el TTL EFECTIVO también se revisa aquí (la
    # config pudo editarse por fuera del gate después de la siembra).
    ok_ttl, _m = ttl_coherente(config)
    if not ok_ttl:
        await _audit(db, pos, "REARM_SKIP", {"motivo": "ttl_incoherente"})
        return "skip:ttl_incoherente"
    if estado is None:
        await _audit(db, pos, "REARM_SKIP", {"motivo": "estado_ilegible"})
        return "skip:estado_ilegible"

    cfg_rearm = normalize_rearm(rearm_config(config)) or {}
    data_symbol = await SymbolMapper().resolve_market_data_symbol(
        db, strategy.asset_symbol)
    inferencia = await obtener_inferencia(
        market_data, data_symbol, opened_at=plan.get("opened_at"),
        timeframe=cfg_rearm.get("timeframe") or "5m", now=now)
    posicion = {"state": pos.state,
                "entry_price": (float(pos.entry_price)
                                if pos.entry_price is not None else None)}
    now_et = _a_et_naive(now)

    cambio = False
    skips_auditados: set[str] = set()
    decision_box: dict = {"d": None}
    resultado = "espera"
    for i in working_idx:
        leg = estado["legs"][i]
        acc = decidir_pierna(leg, estado=estado, posicion=posicion,
                             inferencia=inferencia, cfg_rearm=cfg_rearm,
                             now_et=now_et)
        if acc["accion"] == "ESPERAR":
            continue                       # sin audit: el timing espera cada 60s
        if acc["accion"] == "SKIP":
            clave = str(acc["regla"])
            if clave not in skips_auditados:   # una vez por posición/barrido
                skips_auditados.add(clave)
                await _audit(db, pos, "REARM_SKIP",
                             {"motivo": acc["regla"],
                              "detalle": acc["detalle"]})
            resultado = f"skip:{acc['regla']}"
            continue
        if acc["accion"] == "MATAR":
            estado["legs"][i] = marcar_muerta(leg, str(acc["regla"]))
            await _audit(db, pos, "REARM_KILL",
                         {"leg_index": leg["leg_index"],
                          "regla": acc["regla"], "detalle": acc["detalle"]})
            cambio, resultado = True, "kill"
            continue
        if acc["accion"] == "ASSUMED_FILLED":
            # E2 — SOLO la pierna en el JSON; la posición NO se toca.
            estado["legs"][i] = marcar_assumed_filled(leg)
            await _audit(db, pos, "REARM_ASSUMED",
                         {"leg_index": leg["leg_index"],
                          "regla": acc["regla"], "detalle": acc["detalle"]})
            cambio, resultado = True, "assumed"
            continue
        # REENVIAR
        envio = await _reenviar_pierna(db, pos, config, estado, leg,
                                       settings, now, decision_box)
        if envio == "ok":
            ciclo = int(leg["cycle_n"]) + 1
            base_id = str(pos.entry_signal_id
                          or f"{pos.account_id}:{pos.symbol}")
            estado["legs"][i] = avanzar_ciclo(
                leg, f"{base_id}-r{ciclo}", now.isoformat())
            await _audit(db, pos, "REARM_LEG",
                         {"leg_index": leg["leg_index"], "ciclo": ciclo,
                          "precio": leg["limit_price"], "qty": leg["qty"],
                          "client_id": f"{base_id}-r{ciclo}"})
            cambio, resultado = True, "reenviado"
        elif envio == "sin_qty":
            # el perfil vigente ya no asigna contratos a esta pierna → muere
            # honesta (dejarla working reintentaría un imposible cada 60 s).
            estado["legs"][i] = marcar_muerta(leg, "perfil_sin_qty")
            await _audit(db, pos, "REARM_KILL",
                         {"leg_index": leg["leg_index"],
                          "regla": "perfil_sin_qty",
                          "detalle": "el perfil vigente no asigna qty a la "
                                     "pierna (o viola max_micro_contracts)"})
            cambio, resultado = True, "kill"
        elif envio == "ambiguo":
            # E3 — FAILED AMBIGUO ⇒ MATAR, jamás reintentar: un timeout sin
            # respuesta deja la orden POSIBLEMENTE viva en el broker; un
            # re-envío arriesga tamaño doble. Asimetría de la misión: perder
            # un fill < duplicar tamaño.
            estado["legs"][i] = marcar_muerta(leg, "envio_ambiguo")
            await _audit(db, pos, "REARM_KILL",
                         {"leg_index": leg["leg_index"],
                          "regla": "envio_ambiguo",
                          "detalle": "timeout sin respuesta — posible orden "
                                     "viva; jamás re-enviar sobre ambigüedad "
                                     "(asimetría de la misión: perder un fill "
                                     "< duplicar tamaño)"})
            cambio, resultado = True, "kill"
        else:                              # "fallido" INEQUÍVOCO → reintento
            await _audit(db, pos, "REARM_SKIP",
                         {"motivo": "envio_fallido",
                          "leg_index": leg["leg_index"],
                          "detalle": "todos los destinos FAILED con rechazo "
                                     "INEQUÍVOCO (respuesta HTTP/conexión "
                                     "rechazada) — la orden seguro no existe; "
                                     "el ciclo NO avanza, reintento al "
                                     "siguiente barrido (E3: un fallo ambiguo "
                                     "habría MATADO la pierna)"})
            resultado = "skip:envio_fallido"

    if cambio:
        estado["updated_at"] = now.isoformat()
        await PositionService().set_rearm_state(
            db, pos.strategy_id, pos.account_id, pos.symbol, estado)
    return resultado


async def rearm_sweep(settings, market_data, *, session_factory=None,
                      now: datetime | None = None) -> dict:
    """El barrido (cada 60 s). Candidatas = TODAS las posiciones (el estado lo
    juzga el motor: R-RA5 mata re-armados en posiciones no-abiertas; las que
    no tienen "rearm" sembrado salen gratis en procesar_posicion). Cada una
    en SU sesión/transacción; una excepción no tumba el barrido ni al resto."""
    if session_factory is None:
        from app.db.session import AsyncSessionLocal
        session_factory = AsyncSessionLocal
    now = now or _utcnow()

    async with session_factory() as db:
        rows = await db.execute(select(PositionState.id))
        ids = [r for (r,) in rows.all()]

    resumen: dict = {"posiciones": len(ids), "errores": 0}
    for pid in ids:
        try:
            async with session_factory() as db:
                r = await procesar_posicion(db, pid, settings, market_data,
                                            now=now)
                await db.commit()
            if r:
                resumen[r] = resumen.get(r, 0) + 1
        except Exception as exc:           # fail-closed: rollback + continúa
            resumen["errores"] += 1
            logger.error("rearm_position_failed id={} error={}", pid, exc)
            try:
                async with session_factory() as db2:
                    pos = await db2.get(PositionState, pid)
                    if pos is not None:
                        await _audit(db2, pos, "REARM_SKIP",
                                     {"motivo": "error", "error": repr(exc)})
                    await db2.commit()
            except Exception as exc2:      # ni el audit del error tumba el job
                logger.error("rearm_error_audit_failed id={} error={}",
                             pid, exc2)
    return resumen
