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

import hmac
import uuid
from datetime import datetime, timezone
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path, Query, Request
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
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

    # Not a duplicate within the dedup window. Persist and evaluate.
    #
    # The dedupe_key UNIQUE constraint is permanent, but make_dedupe_key is
    # content-only (strategy/ticker/action/sentiment/price/interval — NO time),
    # while Deduplicator.is_duplicate only looks back window_seconds. So a
    # LEGITIMATE signal repeating the same fields outside that window (e.g. the
    # price returns to the same level hours later) collides with the old row,
    # raises IntegrityError, and kills the background task with no decision
    # written. That is NOT a duplicate — give it a fresh unique storage key
    # (content-level dedup already ran above) and process it normally.
    from sqlalchemy import select as _select

    key_exists = await db.scalar(
        _select(NormalizedSignal.id)
        .where(NormalizedSignal.dedupe_key == original_dedupe_key)
        .limit(1)
    )
    if key_exists is not None:
        norm.dedupe_key = f"rk:{uuid.uuid4().hex}"
        logger.info(
            "dedupe_key_rekeyed strategy={} original_key={} (legit repeat "
            "outside dedup window)",
            strategy_id, original_dedupe_key,
        )

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

    # Fase 3 — classify role from the (estimated) position and handle reversals.
    reversal_decision = await _classify_and_handle_reversal(
        db, norm, strategy, config
    )
    if reversal_decision is not None:
        return reversal_decision

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


_LONG_STATES = {"LONG", "PENDING_LONG"}
_SHORT_STATES = {"SHORT", "PENDING_SHORT"}


def _effective_direction(state: str | None) -> str | None:
    """Estimated current direction for reversal detection."""
    if state in _LONG_STATES:
        return "long"
    if state in _SHORT_STATES:
        return "short"
    return None


def _classify_role(action: str, cur_dir: str | None) -> str:
    """Position-aware signal role (Fase 3)."""
    if action == "exit":
        return "exit_short" if cur_dir == "short" else "exit_long"
    if action == "buy":
        return "reversal_to_long" if cur_dir == "short" else "entry_long"
    if action == "sell":
        return "reversal_to_short" if cur_dir == "long" else "entry_short"
    return "unknown"


async def _classify_and_handle_reversal(
    db: "AsyncSession", norm, strategy, config: dict
):
    """Set norm.signal_role from the current position and handle reversals.

    On a reversal (opposite signal to an open position): ALWAYS close the
    current position first (exits are priority). Then, only if allow_reversal,
    fall through to evaluate the opposite entry normally (returns None). If
    allow_reversal is False, record a BLOCK (reversal_not_allowed) and stop.
    Returns a StrategyDecision to short-circuit, or None to continue.
    """
    if not norm.mapped_symbol:
        return None  # symbol_not_mapped is handled by the pipeline (Level 1.4)

    from sqlalchemy import select
    from app.models.position_state import PositionState

    account_id = config.get("account_id", "paper_default")
    res = await db.execute(
        select(PositionState).where(
            PositionState.account_id == account_id,
            PositionState.symbol == norm.mapped_symbol,
        )
    )
    cur = res.scalar_one_or_none()
    cur_dir = _effective_direction(cur.state if cur else None)
    norm.signal_role = _classify_role(norm.action, cur_dir)

    if norm.signal_role not in ("reversal_to_long", "reversal_to_short"):
        return None  # normal entry/exit flow

    # Reversal: close the current position first (always).
    from app.services.forced_exit import dispatch_forced_exit
    await dispatch_forced_exit(db, cur, strategy, config, "reversal", settings)

    if config.get("allow_reversal", False):
        return None  # fall through: evaluate the opposite entry normally

    # allow_reversal=False → close only; do NOT open the opposite entry.
    norm.status = "processed"
    decision = StrategyDecision(
        normalized_signal_id=norm.id, strategy_id=norm.strategy_id,
        outcome="BLOCK", block_reason="reversal_not_allowed", block_level=3,
        pipeline_execution_json={"reversal": "closed_only"},
    )
    db.add(decision)
    await db.flush()
    try:
        from app.services.performance_tracker import PerformanceTracker
        await PerformanceTracker().update(db, norm.strategy_id, decision)
    except Exception as exc:
        logger.error("performance_update_failed strategy={} error={}",
                     norm.strategy_id, exc)
    logger.info("reversal_closed_only strategy={} symbol={}",
                norm.strategy_id, norm.mapped_symbol)
    return decision


def resolve_effective_dry_run(settings_obj: object, config: dict) -> bool:
    """Fase 2 — layered dispatch gate. Returns the EFFECTIVE dry_run flag.

    A real HTTP send to TradersPost happens ONLY when ALL locks are open:
      1. env ``TRADERSPOST_ENABLED`` (server-level master kill-switch),
      2. ``traderspost_enabled`` (merged global AND strategy, from ConfigResolver),
      3. ``dry_run`` is False (merged: any level on -> dry_run).
    In every other case returns True (dry-run, no HTTP) -- safe by default.
    """
    env_enabled = bool(getattr(settings_obj, "TRADERSPOST_ENABLED", False))
    tp_enabled = bool(config.get("traderspost_enabled", False))
    cfg_dry_run = bool(config.get("dry_run", True))
    real_send = env_enabled and tp_enabled and not cfg_dry_run
    return not real_send


async def _dispatch_approved(
    db: AsyncSession,
    norm: NormalizedSignal,
    strategy: object,
    config: dict,
    pipeline_result: object,
    decision: StrategyDecision,
) -> None:
    """Build payload(s), send to TradersPost, record WebhookDelivery, update state.

    Scaled entries (scale_entry mode in {execute, live}) expand into multiple legs
    (C1 market + C2..Cn limit) sharing a common stop; one WebhookDelivery per leg.
    Single-payload behaviour (exits / non-scaled entries) is unchanged.
    """
    from app.services.payload_builder import PayloadBuilder
    from app.services.traderspost_client import TradersPostClient
    from app.services.position_service import PositionService
    from app.models.webhook_delivery import WebhookDelivery

    payloads = PayloadBuilder().build_scaled(norm, strategy, config, pipeline_result)
    webhook_url = config.get("traderspost_webhook_url")
    # Layered gate: env kill-switch AND traderspost_enabled AND not dry_run.
    dry_run = resolve_effective_dry_run(settings, config)
    client = TradersPostClient(settings)

    any_sent = False
    total_qty = 0
    n_legs = len(payloads)
    for leg_idx, payload in enumerate(payloads, start=1):
        result = await client.send(
            webhook_url or "",
            payload,
            signal_role=norm.signal_role or "",
            dry_run=dry_run,
            signal_ts=norm.signal_ts,
        )
        db.add(WebhookDelivery(
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
        ))
        if result.status == "SENT":
            any_sent = True
        try:
            total_qty += int(payload.get("quantity") or 0)
        except (TypeError, ValueError):
            pass
        if n_legs > 1:
            logger.info(
                "dispatch_leg strategy={} leg={}/{} status={} qty={}",
                norm.strategy_id, leg_idx, n_legs, result.status,
                payload.get("quantity"),
            )

    # Update estimated position state (once, aggregate quantity for scaled entries)
    is_exit = norm.action == "exit"
    account_id = config.get("account_id", "paper_default")
    position_service = PositionService()
    if is_exit:
        await position_service.on_exit_approved(
            db, norm.strategy_id, account_id, norm.mapped_symbol
        )
    else:
        direction = "long" if norm.action == "buy" else "short"
        qty = total_qty or (norm.quantity or 1)
        await position_service.on_entry_approved(
            db, norm.strategy_id, account_id, norm.mapped_symbol,
            direction, qty,
            float(norm.price) if norm.price is not None else None,
            norm.id,
        )

    # SENT (not DRY_RUN) → count as dispatched, confirm estimated position
    if any_sent:
        await position_service.on_delivery_confirmed(
            db, norm.strategy_id, account_id, norm.mapped_symbol
        )

    logger.info(
        "dispatch_complete strategy={} legs={} any_sent={}",
        norm.strategy_id, n_legs, any_sent,
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
    # Per-strategy token if set (Anexo 08 — generated in the UI), else the global
    # secret. Constant-time compare; the raw token never appears in logs.
    expected = (
        strategy.webhook_token
        if (strategy and strategy.webhook_token)
        else settings.LUXALGO_WEBHOOK_SECRET
    )
    token_valid = hmac.compare_digest(token, expected or "")

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
