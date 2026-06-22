"""Forced exit dispatch (Fase 4) — autonomous EOD / max-holding / overnight.

Builds a synthetic exit signal + decision and dispatches it through the SAME
Fase-2 gate as LuxAlgo exits, so forced closes are DRY_RUN in safe mode and only
reach TradersPost when armed. Closes only — never opens.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
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
    db: AsyncSession, position, strategy, config: dict, reason: str, settings
):
    """Build + dispatch a synthetic exit for one open position. Returns the result."""
    from app.api.webhooks_luxalgo import resolve_effective_dry_run
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
    payload = PayloadBuilder().build(norm, strategy, config, pr)
    dry_run = resolve_effective_dry_run(settings, config)
    result = await TradersPostClient(settings).send(
        config.get("traderspost_webhook_url") or "", payload,
        signal_role=role, dry_run=dry_run, signal_ts=now,
    )
    db.add(WebhookDelivery(
        decision_id=decision.id, strategy_id=position.strategy_id,
        destination="traderspost", url_masked=result.url_masked,
        payload_json=result.payload_json,
        response_status_code=result.response_status_code,
        response_body=result.response_body, status=result.status,
        attempts=result.attempts, latency_ms=result.latency_ms,
        error_message=result.error_message,
        sent_at=_utcnow() if result.status == "SENT" else None,
    ))

    ps = PositionService()
    await ps.on_exit_approved(db, position.strategy_id, position.account_id,
                              position.symbol)
    if result.status == "SENT":
        await ps.on_delivery_confirmed(db, position.strategy_id,
                                       position.account_id, position.symbol)

    await AuditService().log(
        db, actor="exit_manager", action="FORCED_EXIT",
        object_type="PositionState",
        object_id=f"{position.account_id}:{position.symbol}",
        new_value={"reason": reason, "status": result.status},
    )
    logger.info("forced_exit strategy={} symbol={} reason={} status={}",
                position.strategy_id, position.symbol, reason, result.status)
    return result


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
