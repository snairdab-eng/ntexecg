"""Signals list + detail with full pipeline breakdown."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.decision import StrategyDecision
from app.models.normalized_signal import NormalizedSignal
from app.models.raw_signal import RawSignal
from app.models.strategy import Strategy
from app.models.webhook_delivery import WebhookDelivery
from app.web.common import render, flash_messages

router = APIRouter()

_PAGE_SIZE = 50


@router.get("/ui/signals", response_class=HTMLResponse)
async def list_signals(
    request: Request,
    db: AsyncSession = Depends(get_db),
    strategy: str = "",
    outcome: str = "",
    page: int = 1,
) -> HTMLResponse:
    stmt = (
        select(StrategyDecision, NormalizedSignal)
        .join(NormalizedSignal, StrategyDecision.normalized_signal_id == NormalizedSignal.id)
        .order_by(StrategyDecision.created_at.desc())
    )
    if strategy:
        stmt = stmt.where(StrategyDecision.strategy_id == strategy)
    if outcome:
        stmt = stmt.where(StrategyDecision.outcome == outcome)

    page = max(1, page)
    stmt = stmt.limit(_PAGE_SIZE).offset((page - 1) * _PAGE_SIZE)
    rows = await db.execute(stmt)
    signals = [
        {
            "id": d.id, "time": d.created_at, "strategy_id": d.strategy_id,
            "ticker_received": s.ticker_received, "mapped_symbol": s.mapped_symbol,
            "action": s.action, "outcome": d.outcome, "score": d.score,
            "block_reason": d.block_reason,
        }
        for d, s in rows.all()
    ]

    strat_rows = await db.execute(select(Strategy.strategy_id).order_by(Strategy.strategy_id))
    strategy_ids = [r[0] for r in strat_rows.all()]

    return await render(
        request, "signals.html",
        {
            "signals": signals, "strategy_ids": strategy_ids,
            "filter_strategy": strategy, "filter_outcome": outcome,
            "page": page, "messages": flash_messages(request),
        }, db=db,
    )


@router.get("/ui/signals/{signal_id}", response_class=HTMLResponse)
async def signal_detail(
    request: Request, signal_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    res = await db.execute(
        select(StrategyDecision, NormalizedSignal)
        .join(NormalizedSignal, StrategyDecision.normalized_signal_id == NormalizedSignal.id)
        .where(StrategyDecision.id == signal_id)
    )
    row = res.first()
    if row is None:
        from app.web.common import redirect
        return redirect("/ui/signals", flash="Señal no encontrada", category="error")
    decision, signal = row

    raw_res = await db.execute(
        select(RawSignal).where(RawSignal.id == signal.raw_signal_id)
    )
    raw = raw_res.scalar_one_or_none()

    deliv_res = await db.execute(
        select(WebhookDelivery).where(WebhookDelivery.decision_id == decision.id)
    )
    delivery = deliv_res.scalar_one_or_none()

    return await render(
        request, "signal_detail.html",
        {
            "decision": decision, "signal": signal,
            "raw": raw, "delivery": delivery,
            "pipeline": decision.pipeline_execution_json or {},
        }, db=db,
    )
