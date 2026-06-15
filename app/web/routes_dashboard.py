"""Dashboard — real metrics from DB + bridge status from app.state."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.decision import StrategyDecision
from app.models.normalized_signal import NormalizedSignal
from app.models.raw_signal import RawSignal
from app.models.strategy import Strategy
from app.models.symbol_map import SymbolMap
from app.models.market_data_status import MarketDataStatus
from app.models.webhook_delivery import WebhookDelivery
from app.web.common import render, flash_messages

router = APIRouter()


def _today_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def _count(db: AsyncSession, stmt) -> int:
    result = await db.execute(stmt)
    return result.scalar_one() or 0


@router.get("/ui", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    today = _today_start()

    received = await _count(
        db, select(func.count(RawSignal.id)).where(RawSignal.received_at >= today)
    )
    approved = await _count(
        db,
        select(func.count(StrategyDecision.id)).where(
            StrategyDecision.created_at >= today,
            StrategyDecision.outcome == "APPROVE",
        ),
    )
    blocked = await _count(
        db,
        select(func.count(StrategyDecision.id)).where(
            StrategyDecision.created_at >= today,
            StrategyDecision.outcome == "BLOCK",
        ),
    )
    sent = await _count(
        db,
        select(func.count(WebhookDelivery.id)).where(
            WebhookDelivery.created_at >= today,
            WebhookDelivery.status == "SENT",
        ),
    )

    # Strategy counts by status group
    rows = await db.execute(
        select(Strategy.status, func.count(Strategy.id)).group_by(Strategy.status)
    )
    by_status: dict[str, int] = {status: count for status, count in rows.all()}
    paper = by_status.get("paper", 0)
    live = by_status.get("live", 0)
    paused = by_status.get("paused", 0)
    active_strats = sum(
        by_status.get(s, 0)
        for s in ("shadow", "paper", "micro", "limited_live", "live")
    )

    # Recent decisions (last 10) joined with signal info
    recent = await db.execute(
        select(StrategyDecision, NormalizedSignal)
        .join(NormalizedSignal, StrategyDecision.normalized_signal_id == NormalizedSignal.id)
        .order_by(StrategyDecision.created_at.desc())
        .limit(10)
    )
    recent_decisions = [
        {
            "time": d.created_at,
            "strategy_id": d.strategy_id,
            "ticker": s.ticker_received,
            "action": s.action,
            "outcome": d.outcome,
            "score": d.score,
            "block_reason": d.block_reason,
        }
        for d, s in recent.all()
    ]

    ctx = {
        "metrics": {
            "received": received, "approved": approved,
            "blocked": blocked, "sent": sent,
        },
        "strategy_counts": {
            "active": active_strats, "paper": paper, "live": live, "paused": paused,
        },
        "recent_decisions": recent_decisions,
        "messages": flash_messages(request),
    }
    return await render(request, "dashboard.html", ctx, db=db)


# ---------------------------------------------------------------------------
# HTMX partials
# ---------------------------------------------------------------------------

async def _bridge_rows(db: AsyncSession) -> list[dict]:
    result = await db.execute(
        select(MarketDataStatus).order_by(MarketDataStatus.symbol)
    )
    statuses = result.scalars().all()
    rows: list[dict] = []
    for st in statuses:
        rows.append({
            "symbol": st.symbol,
            "active": st.is_active,
            "atr_5m": st.last_atr_5m,
            "atr_1h": st.last_atr_1h,
            "heartbeat_age": st.heartbeat_age_seconds,
        })
    return rows


@router.get("/ui/partials/bridge-status", response_class=HTMLResponse)
async def bridge_status_partial(
    request: Request, db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    from app.web.common import templates
    rows = await _bridge_rows(db)
    return templates.TemplateResponse(
        request, "partials/bridge_status.html", {"rows": rows}
    )


@router.get("/ui/partials/bridge-badge", response_class=HTMLResponse)
async def bridge_badge_partial(
    request: Request, db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    from app.web.common import templates
    rows = await _bridge_rows(db)
    total = len(rows)
    active = sum(1 for r in rows if r["active"])
    if total == 0:
        color, label = "bg-gray-500", "Bridge"
    elif active == total:
        color, label = "bg-green-500", "Bridge"
    elif active == 0:
        color, label = "bg-red-500", "Bridge ⚠"
    else:
        color, label = "bg-orange-500", "Bridge ⚠"
    return templates.TemplateResponse(
        request, "partials/bridge_badge.html",
        {"color": color, "label": label, "active": active, "total": total},
    )


@router.get("/ui/partials/recent-signals", response_class=HTMLResponse)
async def recent_signals_partial(
    request: Request, db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    from app.web.common import templates
    result = await db.execute(
        select(StrategyDecision, NormalizedSignal)
        .join(NormalizedSignal, StrategyDecision.normalized_signal_id == NormalizedSignal.id)
        .order_by(StrategyDecision.created_at.desc())
        .limit(5)
    )
    signals = [
        {
            "time": d.created_at, "strategy_id": d.strategy_id,
            "ticker": s.ticker_received, "action": s.action,
            "outcome": d.outcome, "score": d.score, "block_reason": d.block_reason,
        }
        for d, s in result.all()
    ]
    return templates.TemplateResponse(
        request, "partials/recent_signals.html", {"signals": signals}
    )
