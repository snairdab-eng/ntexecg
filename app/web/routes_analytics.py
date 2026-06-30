"""Analytics — aggregated charts over decisions + deliveries.

Read-only. Everything is computed from StrategyDecision / RawSignal /
WebhookDelivery so it reflects exactly what the pipeline did. The template
renders the numbers with Chart.js (client-side) from a JSON blob.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.decision import StrategyDecision
from app.models.raw_signal import RawSignal
from app.models.webhook_delivery import WebhookDelivery
from app.web.common import flash_messages, render

router = APIRouter()


@router.get("/ui/analytics", response_class=HTMLResponse)
async def analytics(
    request: Request,
    db: AsyncSession = Depends(get_db),
    days: int = 14,
) -> HTMLResponse:
    days = max(1, min(days, 90))
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)

    # ── Outcomes breakdown (APPROVE / BLOCK / others) ────────────────────────
    rows = await db.execute(
        select(StrategyDecision.outcome, func.count(StrategyDecision.id))
        .where(StrategyDecision.created_at >= since)
        .group_by(StrategyDecision.outcome)
    )
    outcomes = {o or "UNKNOWN": c for o, c in rows.all()}

    # ── Block reasons (why NOT approved), top 12 ─────────────────────────────
    rows = await db.execute(
        select(StrategyDecision.block_reason, func.count(StrategyDecision.id))
        .where(
            StrategyDecision.created_at >= since,
            StrategyDecision.outcome == "BLOCK",
        )
        .group_by(StrategyDecision.block_reason)
        .order_by(func.count(StrategyDecision.id).desc())
        .limit(12)
    )
    block_reasons = [
        {"reason": r or "(sin motivo)", "count": c} for r, c in rows.all()
    ]

    # ── Blocks by pipeline level (WHERE it stopped) ──────────────────────────
    rows = await db.execute(
        select(StrategyDecision.block_level, func.count(StrategyDecision.id))
        .where(
            StrategyDecision.created_at >= since,
            StrategyDecision.outcome == "BLOCK",
        )
        .group_by(StrategyDecision.block_level)
        .order_by(StrategyDecision.block_level)
    )
    level_names = {
        1: "N1 Sistema", 2: "N2 Temporal", 3: "N3 Riesgo",
        4: "N4 Score/Régimen", 5: "N5 SL/TP",
    }
    blocks_by_level = [
        {"level": level_names.get(lv, f"N{lv}" if lv else "—"), "count": c}
        for lv, c in rows.all()
    ]

    # ── Per-strategy approve vs block ────────────────────────────────────────
    rows = await db.execute(
        select(
            StrategyDecision.strategy_id,
            StrategyDecision.outcome,
            func.count(StrategyDecision.id),
        )
        .where(StrategyDecision.created_at >= since)
        .group_by(StrategyDecision.strategy_id, StrategyDecision.outcome)
    )
    per_strat: dict[str, dict[str, int]] = {}
    for sid, outcome, c in rows.all():
        d = per_strat.setdefault(sid or "—", {"APPROVE": 0, "BLOCK": 0, "OTHER": 0})
        if outcome in ("APPROVE", "BLOCK"):
            d[outcome] += c
        else:
            d["OTHER"] += c
    by_strategy = sorted(
        (
            {
                "strategy_id": sid,
                "approve": v["APPROVE"],
                "block": v["BLOCK"],
                "other": v["OTHER"],
                "total": v["APPROVE"] + v["BLOCK"] + v["OTHER"],
            }
            for sid, v in per_strat.items()
        ),
        key=lambda x: x["total"],
        reverse=True,
    )

    # ── Daily time series: received / approved / sent ────────────────────────
    day_labels: list[str] = []
    buckets: dict[str, dict[str, int]] = {}
    for i in range(days - 1, -1, -1):
        d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        day_labels.append(d)
        buckets[d] = {"received": 0, "approved": 0, "sent": 0}

    def _bump(rows_, key):
        for ts, c in rows_:
            if ts is None:
                continue
            day = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)[:10]
            if day in buckets:
                buckets[day][key] += c

    r1 = await db.execute(
        select(func.date(RawSignal.received_at), func.count(RawSignal.id))
        .where(RawSignal.received_at >= since)
        .group_by(func.date(RawSignal.received_at))
    )
    _bump(r1.all(), "received")
    r2 = await db.execute(
        select(func.date(StrategyDecision.created_at), func.count(StrategyDecision.id))
        .where(
            StrategyDecision.created_at >= since,
            StrategyDecision.outcome == "APPROVE",
        )
        .group_by(func.date(StrategyDecision.created_at))
    )
    _bump(r2.all(), "approved")
    r3 = await db.execute(
        select(func.date(WebhookDelivery.created_at), func.count(WebhookDelivery.id))
        .where(
            WebhookDelivery.created_at >= since,
            WebhookDelivery.status == "SENT",
        )
        .group_by(func.date(WebhookDelivery.created_at))
    )
    _bump(r3.all(), "sent")

    timeseries = {
        "labels": day_labels,
        "received": [buckets[d]["received"] for d in day_labels],
        "approved": [buckets[d]["approved"] for d in day_labels],
        "sent": [buckets[d]["sent"] for d in day_labels],
    }

    # ── Delivery status breakdown ────────────────────────────────────────────
    rows = await db.execute(
        select(WebhookDelivery.status, func.count(WebhookDelivery.id))
        .where(WebhookDelivery.created_at >= since)
        .group_by(WebhookDelivery.status)
    )
    delivery_status = {s or "—": c for s, c in rows.all()}

    total_dec = sum(outcomes.values())
    approve_n = outcomes.get("APPROVE", 0)
    chart_data = {
        "outcomes": outcomes,
        "block_reasons": block_reasons,
        "blocks_by_level": blocks_by_level,
        "by_strategy": by_strategy,
        "timeseries": timeseries,
        "delivery_status": delivery_status,
    }
    summary = {
        "days": days,
        "total": total_dec,
        "approved": approve_n,
        "blocked": outcomes.get("BLOCK", 0),
        "approval_rate": round(100 * approve_n / total_dec, 1) if total_dec else 0.0,
        "sent": delivery_status.get("SENT", 0),
        "failed": delivery_status.get("FAILED", 0),
    }

    return await render(
        request,
        "analytics.html",
        {
            "chart_data": chart_data,
            "summary": summary,
            "by_strategy": by_strategy,
            "block_reasons": block_reasons,
            "days": days,
            "messages": flash_messages(request),
        },
        db=db,
    )
