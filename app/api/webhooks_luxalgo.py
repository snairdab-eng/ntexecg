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
from typing import Any

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
) -> StrategyDecision:
    """Normalize → deduplicate → route signal. Returns the StrategyDecision.

    Always saves a NormalizedSignal. Duplicates get a "dup:" prefixed dedupe_key
    (unique UUID suffix) to satisfy the FK constraint while preserving audit trail.
    """
    normalizer = SignalNormalizer()
    norm = await normalizer.normalize(db, raw_signal_id, strategy_id, body)
    original_dedupe_key = norm.dedupe_key

    # Deduplicate BEFORE saving — only checks already-persisted rows
    deduplicator = Deduplicator()
    if await deduplicator.is_duplicate(db, original_dedupe_key):
        # Prefix makes this key unique while signalling it's a dup
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

    # Not a duplicate: persist and route
    db.add(norm)
    await db.flush()

    strategy = await get_strategy_by_id(db, strategy_id)

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
        outcome, block_reason, block_level, score = "QUEUE_FOR_REVIEW", "strategy_candidate", 1, None

    elif strategy.status == "candidate":
        outcome, block_reason, block_level, score = "QUEUE_FOR_REVIEW", "strategy_candidate", 1, None

    elif strategy.status == "quarantined":
        outcome, block_reason, block_level, score = "BLOCK", "strategy_quarantined", 3, None

    elif strategy.status == "retired":
        outcome, block_reason, block_level, score = "BLOCK", "strategy_retired", 5, None

    elif strategy.status == "paused" and norm.action in ("buy", "sell"):
        outcome, block_reason, block_level, score = "BLOCK", "strategy_paused", 2, None

    else:
        # shadow / paper / micro / limited_live / live
        # Phase 1 stub: APPROVE with score=100; full FilterPipeline in Phase 2
        outcome, block_reason, block_level, score = "APPROVE", None, None, 100

    norm.status = "processed"
    decision = StrategyDecision(
        normalized_signal_id=norm.id,
        strategy_id=strategy_id,
        outcome=outcome,
        block_reason=block_reason,
        block_level=block_level,
        score=score,
    )
    db.add(decision)
    return decision


# ---------------------------------------------------------------------------
# Background wrapper — creates its own session (request session is closed)
# ---------------------------------------------------------------------------

async def _background_process_signal(
    strategy_id: str,
    raw_signal_id_str: str,
    body: dict,
) -> None:
    factory = _get_bg_factory()
    async with factory() as db:
        try:
            await process_signal(db, strategy_id, uuid.UUID(raw_signal_id_str), body)
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

    background_tasks.add_task(
        _background_process_signal, strategy_id, str(raw_signal.id), body
    )
    return {"received": True, "signal_id": str(raw_signal.id)}
