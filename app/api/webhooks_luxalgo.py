"""Webhook receiver for LuxAlgo Backtesting AI signals.

Flow per request:
  1. Parse body
  2. Validate token (per-strategy hash, fallback to global dev secret)
  3. Save RawSignal — ALWAYS, even on invalid token (audit trail)
  4. Return 401 if token invalid (+ AuditLog)
  5. Return 200 immediately with signal_id
  6. Background task: process_signal() — normalize → dedupe → route

process_signal() is a standalone async function so tests can call it directly
without going through the HTTP layer.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path, Query, Request
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import verify_token
from app.db.session import AsyncSessionLocal, get_db
from app.models.decision import StrategyDecision
from app.models.normalized_signal import NormalizedSignal
from app.models.raw_signal import RawSignal
from app.services.deduplicator import Deduplicator
from app.services.repositories import (
    create_audit_log,
    create_strategy,
    get_strategy_by_id,
)
from app.services.signal_normalizer import SignalNormalizer
from app.services.market_data_service import MarketDataService, get_market_data_service

router = APIRouter()

# Overrideable in tests to inject the test session factory
_bg_session_factory: Any = None


def _get_bg_factory() -> Any:
    return _bg_session_factory or AsyncSessionLocal


# ---------------------------------------------------------------------------
# Core signal processing — testable without HTTP layer
# ---------------------------------------------------------------------------

async def process_signal(
    db: AsyncSession,
    strategy_id: str,
    raw_signal_id: uuid.UUID,
    body: dict,
    market_data: "MarketDataService",
) -> StrategyDecision:
    """Normalize → deduplicate → FilterPipeline → Decision.

    Always saves a NormalizedSignal and StrategyDecision.
    Duplicates get a "dup:" prefixed dedupe_key for UNIQUE constraint.

    market_data is injected (never instantiated here) so the provider is the
    one selected at startup, and tests can pass MockMarketDataProvider.
    """
    normalizer = SignalNormalizer()
    norm = await normalizer.normalize(db, raw_signal_id, strategy_id, body)
    original_dedupe_key = norm.dedupe_key

    # Deduplicate BEFORE saving
    deduplicator = Deduplicator()
    if await deduplicator.is_duplicate(db, original_dedupe_key):
        norm.dedupe_key = f"dup:{uuid.uuid4().hex}"
        norm.status = "duplicate"
        db.add(norm)
        await db.flush()
        decision = StrategyDecision(
            normalized_signal_id=norm.id,
            strategy_id=strategy_id,
            outcome="IGNORE_DUPLICATE",
            block_reason="duplicate_signal",
            block_level=1,
        )
        db.add(decision)
        logger.debug(
            "duplicate_signal strategy={} original_key={}",
            strategy_id, original_dedupe_key,
        )
        return decision

    # Not a duplicate: persist and evaluate through pipeline
    db.add(norm)
    await db.flush()

    strategy = await get_strategy_by_id(db, strategy_id)

    # Auto-create strategy if unknown
    if strategy is None:
        strategy = await create_strategy(db, strategy_id, strategy_id, None)
        await create_audit_log(
            db,
            actor="system",
            action="CREATE",
            object_type="Strategy",
            object_id=strategy_id,
            reason="auto_created_from_unknown_signal",
        )
        logger.info("strategy_auto_created strategy_id={}", strategy_id)

    # Run through FilterPipeline (market_data injected by caller)
    from app.services.filter_pipeline import FilterPipeline
    from app.services.config_resolver import ConfigResolver

    pipeline = FilterPipeline(market_data)

    # AssetProfile is keyed by the base ticker ("MES"), which is exactly
    # ticker_received — NOT the mapped contract ("MESU2025"). Passing
    # mapped_symbol here would silently skip all asset-level config
    # (session hours, sl_atr_multiplier, daily_loss_stop).
    config = await ConfigResolver().resolve(db, strategy_id, norm.ticker_received)

    pipeline_result = await pipeline.evaluate(db, norm, strategy, config)

    norm.status = "processed"
    decision = StrategyDecision(
        normalized_signal_id=norm.id,
        strategy_id=strategy_id,
        outcome=pipeline_result.outcome,
        block_reason=pipeline_result.block_reason,
        block_level=pipeline_result.block_level,
        score=pipeline_result.score,
        sl_price=pipeline_result.sl_price,
        tp_price=pipeline_result.tp_price,
        atr_value=pipeline_result.atr_value,
        market_data_provider=pipeline_result.market_data_provider,
        pipeline_execution_json=pipeline_result.pipeline_execution_json,
    )
    db.add(decision)
    await db.flush()  # decision.id needed for WebhookDelivery FK
    logger.info(
        "signal_evaluated strategy={} mapped_symbol={} outcome={} score={}",
        strategy_id, norm.mapped_symbol, pipeline_result.outcome,
        pipeline_result.score,
    )

    # Track performance metrics for every decision (never blocks the flow)
    from app.services.performance_tracker import PerformanceTracker
    try:
        await PerformanceTracker().update(db, strategy_id, decision)
    except Exception as exc:
        logger.error("performance_update_failed strategy={} error={}", strategy_id, exc)

    # Dispatch to TradersPost only on APPROVE
    if pipeline_result.outcome == "APPROVE":
        await _dispatch_approved(db, norm, strategy, config, pipeline_result, decision)

    return decision


async def _dispatch_approved(
    db: AsyncSession,
    norm: NormalizedSignal,
    strategy: object,
    config: dict,
    pipeline_result: object,
    decision: StrategyDecision,
) -> None:
    """Build payload, send to TradersPost, record WebhookDelivery, update state."""
    from app.services.payload_builder import PayloadBuilder
    from app.services.traderspost_client import TradersPostClient
    from app.services.position_service import PositionService
    from app.models.webhook_delivery import WebhookDelivery

    payload = PayloadBuilder().build(norm, strategy, config, pipeline_result)
    webhook_url = config.get("traderspost_webhook_url")
    dry_run = config.get("dry_run", True)

    client = TradersPostClient(settings)
    result = await client.send(
        webhook_url or "",
        payload,
        signal_role=norm.signal_role or "",
        dry_run=dry_run,
        signal_ts=norm.signal_ts,
    )

    delivery = WebhookDelivery(
        decision_id=decision.id,
        strategy_id=norm.strategy_id,
        destination="traderspost",
        url_masked=result.url_masked,
        payload_json=result.payload_json,
        response_status_code=result.response_status_code,
        response_body=result.response_body,
        status=result.status,
        attempts=result.attempts,
        latency_ms=result.latency_ms,
        error_message=result.error_message,
        sent_at=_utcnow() if result.status == "SENT" else None,
    )
    db.add(delivery)

    # Update estimated position state
    is_exit = norm.action == "exit"
    account_id = config.get("account_id", "paper_default")
    position_service = PositionService()
    if is_exit:
        await position_service.on_exit_approved(
            db, norm.strategy_id, account_id, norm.mapped_symbol
        )
    else:
        direction = "long" if norm.action == "buy" else "short"
        await position_service.on_entry_approved(
            db, norm.strategy_id, account_id, norm.mapped_symbol,
            direction, norm.quantity or 1,
            float(norm.price) if norm.price is not None else None,
            norm.id,
        )

    # SENT (not DRY_RUN) → count as dispatched, confirm estimated position
    if result.status == "SENT":
        decision_perf_symbol = norm.mapped_symbol
        await position_service.on_delivery_confirmed(
            db, norm.strategy_id, account_id, decision_perf_symbol
        )

    logger.info(
        "dispatch_complete strategy={} status={} attempts={}",
        norm.strategy_id, result.status, result.attempts,
    )


# ---------------------------------------------------------------------------
# Background wrapper — creates its own session (request session is closed)
# ---------------------------------------------------------------------------

async def _background_process_signal(
    strategy_id: str,
    raw_signal_id_str: str,
    body: dict,
    market_data: MarketDataService,
) -> None:
    factory = _get_bg_factory()
    async with factory() as db:
        try:
            await process_signal(
                db, strategy_id, uuid.UUID(raw_signal_id_str), body, market_data
            )
            await db.commit()
        except Exception as exc:
            logger.error(
                "process_signal_failed strategy={} raw_signal_id={} error={}",
                strategy_id, raw_signal_id_str, exc,
            )
            await db.rollback()


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/webhooks/luxalgo/{strategy_id}")
async def receive_luxalgo_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    strategy_id: str = Path(...),
    token: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Receive a LuxAlgo signal.

    Returns 200 immediately; signal processing runs in a background task.
    Token is NEVER logged in plain text — only its validity is logged.
    """
    body = await request.json()
    client_ip = request.client.host if request.client else None

    # Token validation — raw token never appears in logs
    strategy = await get_strategy_by_id(db, strategy_id)
    if strategy and strategy.webhook_token:
        token_valid = verify_token(token, settings.WEBHOOK_TOKEN_SALT, strategy.webhook_token)
    else:
        # No per-strategy token configured: validate against global dev secret
        token_valid = (token == settings.LUXALGO_WEBHOOK_SECRET)

    # Save RawSignal ALWAYS (audit trail even for invalid tokens)
    raw_signal = RawSignal(
        source="luxalgo",
        strategy_id=strategy_id,
        ticker_received=body.get("ticker"),
        action=body.get("action"),
        sentiment=body.get("sentiment"),
        quantity_raw=str(body.get("quantity", "")),
        price_raw=str(body.get("price", "")),
        time_raw=str(body.get("time", "")),
        interval_raw=str(body.get("interval", "")),
        payload_json=body,
        ip_address=client_ip,
        token_valid=token_valid,
    )
    db.add(raw_signal)
    await db.commit()
    await db.refresh(raw_signal)

    if not token_valid:
        await create_audit_log(
            db,
            actor="system",
            action="WEBHOOK_BLOCKED",
            object_type="System",
            object_id=strategy_id,
            reason="invalid_token",
            ip_address=client_ip,
        )
        await db.commit()
        logger.warning(
            "webhook_invalid_token strategy={} ip={}", strategy_id, client_ip,
        )
        raise HTTPException(status_code=401, detail="Invalid token")

    logger.info(
        "webhook_received strategy={} ticker={} action={} sentiment={}",
        strategy_id, body.get("ticker"), body.get("action"), body.get("sentiment"),
    )

    # Use the MarketDataService selected at startup (app.state). Fall back to
    # building from settings if lifespan didn't populate it (e.g. some test setups).
    market_data = getattr(request.app.state, "market_data", None)
    if market_data is None:
        market_data = get_market_data_service(settings)

    background_tasks.add_task(
        _background_process_signal,
        strategy_id, str(raw_signal.id), body, market_data,
    )
    return {"received": True, "signal_id": str(raw_signal.id)}
