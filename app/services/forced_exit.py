"""Forced exit dispatch (Fase 4) — autonomous EOD / max-holding / overnight.

Builds a synthetic exit signal + decision and dispatches it through the SAME
Fase-2 gate as LuxAlgo exits, so forced closes are DRY_RUN in safe mode and only
reach TradersPost when armed. Closes only — never opens.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.decision import StrategyDecision
from app.models.normalized_signal import NormalizedSignal
from app.models.position_state import PositionState
from app.models.raw_signal import RawSignal
from app.models.webhook_delivery import WebhookDelivery
from app.services.exit_manager import ExitManager, OPEN_STATES


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def dispatch_forced_exit(
    db: AsyncSession, position, strategy, config: dict, reason: str, settings,
    actor: str = "exit_manager",
):
    """Build + dispatch a synthetic exit for one open position.

    NX-07: el cierre se envía a TODOS los destinos habilitados (base + perfiles
    de riesgo, via dispatch_profiles) — si la entrada se replicó por perfil, el
    cierre también. El gate Fase-2 se evalúa POR destino (un perfil solo puede
    restringir, NX-02). Un WebhookDelivery por destino.
    NX-08: si ningún destino quedó SENT y hubo FAILED reales, la posición pasa
    a UNKNOWN (estado incierto → L3 bloquea entradas) en vez de EXITING eterno.
    Devuelve el resultado del destino BASE (el primero).
    """
    from app.api.webhooks_luxalgo import resolve_effective_dry_run
    from app.services import dispatch_profiles as dprof
    from app.services.audit_service import AuditService
    from app.services.payload_builder import PayloadBuilder
    from app.services.position_service import PositionService
    from app.services.traderspost_client import TradersPostClient

    now = _utcnow()
    direction = position.direction or ("long" if position.state == "LONG" else "short")
    role = "exit_long" if direction == "long" else "exit_short"

    raw = RawSignal(
        source="exit_manager", strategy_id=position.strategy_id,
        ticker_received=position.symbol, action="exit", sentiment="flat",
        quantity_raw=str(position.quantity or 0),
        payload_json={"forced_exit": reason}, token_valid=True,
    )
    db.add(raw)
    await db.flush()
    norm = NormalizedSignal(
        raw_signal_id=raw.id, source="exit_manager", strategy_id=position.strategy_id,
        ticker_received=position.symbol, mapped_symbol=position.symbol,
        action="exit", sentiment="flat", quantity=position.quantity or 0,
        signal_ts=now, signal_role=role,
        dedupe_key=f"forced:{uuid.uuid4().hex}", status="processed",
    )
    db.add(norm)
    await db.flush()
    decision = StrategyDecision(
        normalized_signal_id=norm.id, strategy_id=position.strategy_id,
        outcome="EXIT_ONLY", reason_detail=f"forced_exit:{reason}",
        pipeline_execution_json={"forced_exit": reason},
    )
    db.add(decision)
    await db.flush()

    pr = SimpleNamespace(sl_price=None, tp_price=None, atr_value=None,
                         score=None, market_data_provider=None)
    # El payload de salida es idéntico para todos los destinos (sin SL/TP,
    # solo cierre) — se construye una vez.
    payload = PayloadBuilder().build(norm, strategy, config, pr)

    client = TradersPostClient(settings)
    destinations = dprof.resolve_destinations(config)
    first_result = None
    any_sent = any_failed = False
    for dest in destinations:
        dest_config = dprof.make_dest_config(config, dest)
        dry_run = resolve_effective_dry_run(settings, dest_config)
        result = await client.send(
            dest["webhook_url"] or "", payload,
            signal_role=role, dry_run=dry_run, signal_ts=now,
        )
        db.add(WebhookDelivery(
            decision_id=decision.id, strategy_id=position.strategy_id,
            destination=dprof.delivery_tag(dest["name"]),
            url_masked=result.url_masked,
            payload_json=result.payload_json,
            response_status_code=result.response_status_code,
            response_body=result.response_body, status=result.status,
            attempts=result.attempts, latency_ms=result.latency_ms,
            error_message=result.error_message,
            sent_at=_utcnow() if result.status == "SENT" else None,
        ))
        if first_result is None:
            first_result = result
        if result.status == "SENT":
            any_sent = True
        if result.status == "FAILED":
            any_failed = True

    ps = PositionService()
    await ps.on_exit_approved(db, position.strategy_id, position.account_id,
                              position.symbol)
    if any_sent:
        await ps.on_delivery_confirmed(db, position.strategy_id,
                                       position.account_id, position.symbol)
    elif any_failed:
        # NX-08 — envío real fallido en todos los destinos: estado incierto.
        await ps.on_exit_failed(db, position.strategy_id,
                                position.account_id, position.symbol,
                                actor=actor)

    await AuditService().log(
        db, actor=actor, action="FORCED_EXIT",
        object_type="PositionState",
        object_id=f"{position.account_id}:{position.symbol}",
        new_value={"reason": reason, "status": first_result.status,
                   "destinations": len(destinations), "any_sent": any_sent},
    )
    logger.info(
        "forced_exit strategy={} symbol={} reason={} status={} destinations={}",
        position.strategy_id, position.symbol, reason, first_result.status,
        len(destinations),
    )
    return first_result


# NX-08 — estados transitorios que no deben quedarse pegados: PENDING_* espera
# confirmación de entrada, EXITING espera confirmación de cierre. Si superan
# esta antigüedad, algo se perdió (delivery FAILED sin transición, restart a
# media operación) y el operador debe revisar.
_STALE_STATES = ("PENDING_LONG", "PENDING_SHORT", "EXITING")
STALE_AFTER_MINUTES = 15


async def find_stale_positions(
    db: AsyncSession,
    older_than_minutes: int = STALE_AFTER_MINUTES,
    now: datetime | None = None,
) -> list[PositionState]:
    """Posiciones en estado transitorio (PENDING_*/EXITING) sin actualizar hace
    más de `older_than_minutes` — el ExitManagerJob las reporta con warning."""
    now = now or _utcnow()
    cutoff = now - timedelta(minutes=older_than_minutes)
    rows = await db.execute(
        select(PositionState).where(
            PositionState.state.in_(list(_STALE_STATES)),
            PositionState.updated_at < cutoff,
        )
    )
    return list(rows.scalars().all())


async def exit_manager_sweep(db: AsyncSession, settings, now: datetime | None = None) -> int:
    """Scan open positions and dispatch forced exits for the ones that are due."""
    from app.services.config_resolver import ConfigResolver
    from app.services.repositories import get_strategy_by_id

    em = ExitManager()
    resolver = ConfigResolver()
    rows = await db.execute(
        select(PositionState).where(PositionState.state.in_(list(OPEN_STATES)))
    )
    dispatched = 0
    for pos in rows.scalars().all():
        if not pos.strategy_id:
            continue
        strategy = await get_strategy_by_id(db, pos.strategy_id)
        if strategy is None:
            continue
        config = await resolver.resolve(db, pos.strategy_id, strategy.asset_symbol)
        reason = em.due_exit(pos, config, now=now)
        if reason:
            await dispatch_forced_exit(db, pos, strategy, config, reason, settings)
            dispatched += 1
    return dispatched
