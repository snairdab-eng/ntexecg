"""Dashboard UNIFICADO — métricas + charts sobre decisiones/entregas.

P2 de la auditoría (2026-07-06): Dashboard y Analítica compartían el mismo
universo de datos (StrategyDecision/RawSignal/WebhookDelivery) con distinta
ventana (hoy vs N días). Esta página los fusiona: selector de rango
{hoy, 7, 14, 30, 90 días} + fila operacional (bridge, entregas, estrategias)
+ los charts de la vieja Analítica (Chart.js desde un blob JSON, read-only).
`/ui/analytics` redirige aquí (bookmarks/links viejos no se rompen).

⚠ Los partials HTMX de abajo son LOAD-BEARING app-wide: `base.html:72`
consume `/ui/partials/bridge-badge` en el navbar de TODA la app — los
partials NO se tocan, solo se fusionó la página principal.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.decision import StrategyDecision
from app.models.normalized_signal import NormalizedSignal
from app.models.raw_signal import RawSignal
from app.models.strategy import Strategy
from app.models.market_data_status import MarketDataStatus
from app.models.webhook_delivery import WebhookDelivery
from app.services.strategy_aliases import canonical_id, get_alias_map
from app.web.common import render, flash_messages

router = APIRouter()

# Rangos del selector: 0 = hoy (desde medianoche UTC), el resto en días.
RANGOS_DIAS = (0, 7, 14, 30, 90)


def _today_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def _count(db: AsyncSession, stmt) -> int:
    result = await db.execute(stmt)
    return result.scalar_one() or 0


@router.get("/ui", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db),
                    days: int = 0) -> HTMLResponse:
    days = days if days in RANGOS_DIAS else 0
    now = datetime.now(timezone.utc)
    since = _today_start() if days == 0 else now - timedelta(days=days)

    # ── KPIs del rango ───────────────────────────────────────────────────
    received = await _count(
        db, select(func.count(RawSignal.id)).where(RawSignal.received_at >= since)
    )
    rows = await db.execute(
        select(StrategyDecision.outcome, func.count(StrategyDecision.id))
        .where(StrategyDecision.created_at >= since)
        .group_by(StrategyDecision.outcome)
    )
    outcomes = {o or "UNKNOWN": c for o, c in rows.all()}

    # ── Motivos de bloqueo (por qué NO se aprobó), top 12 ────────────────
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

    # ── Bloqueos por nivel del pipeline (DÓNDE se detuvo) ────────────────
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

    # ── Por estrategia (NX-24: agrupar por id CANÓNICO — los renames no
    # parten la serie; marcar retiradas/huérfanas) ────────────────────────
    rows = await db.execute(
        select(
            StrategyDecision.strategy_id,
            StrategyDecision.outcome,
            func.count(StrategyDecision.id),
        )
        .where(StrategyDecision.created_at >= since)
        .group_by(StrategyDecision.strategy_id, StrategyDecision.outcome)
    )
    alias_map = await get_alias_map(db)
    per_strat: dict[str, dict[str, int]] = {}
    for sid, outcome, c in rows.all():
        cid = canonical_id(sid, alias_map)
        d = per_strat.setdefault(cid, {"APPROVE": 0, "BLOCK": 0, "OTHER": 0})
        if outcome in ("APPROVE", "BLOCK"):
            d[outcome] += c
        else:
            d["OTHER"] += c

    status_rows = await db.execute(
        select(Strategy.strategy_id, Strategy.status)
    )
    status_by_id = dict(status_rows.all())

    by_strategy = sorted(
        (
            {
                "strategy_id": sid,
                "approve": v["APPROVE"],
                "block": v["BLOCK"],
                "other": v["OTHER"],
                "total": v["APPROVE"] + v["BLOCK"] + v["OTHER"],
                "retired": status_by_id.get(sid) == "retired",
                "missing": sid not in status_by_id,
            }
            for sid, v in per_strat.items()
        ),
        key=lambda x: x["total"],
        reverse=True,
    )

    # ── Serie diaria recibidas/aprobadas/enviadas (solo rango multi-día:
    # para "hoy" una serie de un punto no dice nada — se omite) ───────────
    timeseries = None
    if days >= 2:
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
                day = (ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime")
                       else str(ts)[:10])
                if day in buckets:
                    buckets[day][key] += c

        r1 = await db.execute(
            select(func.date(RawSignal.received_at), func.count(RawSignal.id))
            .where(RawSignal.received_at >= since)
            .group_by(func.date(RawSignal.received_at))
        )
        _bump(r1.all(), "received")
        r2 = await db.execute(
            select(func.date(StrategyDecision.created_at),
                   func.count(StrategyDecision.id))
            .where(
                StrategyDecision.created_at >= since,
                StrategyDecision.outcome == "APPROVE",
            )
            .group_by(func.date(StrategyDecision.created_at))
        )
        _bump(r2.all(), "approved")
        r3 = await db.execute(
            select(func.date(WebhookDelivery.created_at),
                   func.count(WebhookDelivery.id))
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

    # ── Entregas TradersPost del rango ───────────────────────────────────
    rows = await db.execute(
        select(WebhookDelivery.status, func.count(WebhookDelivery.id))
        .where(WebhookDelivery.created_at >= since)
        .group_by(WebhookDelivery.status)
    )
    delivery_status = {s or "—": c for s, c in rows.all()}

    # ── Fila operacional: estrategias por estado (independiente del rango)
    rows = await db.execute(
        select(Strategy.status, func.count(Strategy.id)).group_by(Strategy.status)
    )
    by_status: dict[str, int] = {status: count for status, count in rows.all()}
    strategy_counts = {
        "active": sum(by_status.get(s, 0)
                      for s in ("shadow", "paper", "micro", "limited_live",
                                "live")),
        "paper": by_status.get("paper", 0),
        "live": by_status.get("live", 0),
        "paused": by_status.get("paused", 0),
    }

    # ── Decisiones recientes (últimas 10, independiente del rango) — el id
    # se muestra CANÓNICO (NX-24, coherente con la tabla por estrategia) ──
    recent = await db.execute(
        select(StrategyDecision, NormalizedSignal)
        .join(NormalizedSignal,
              StrategyDecision.normalized_signal_id == NormalizedSignal.id)
        .order_by(StrategyDecision.created_at.desc())
        .limit(10)
    )
    recent_decisions = [
        {
            "time": d.created_at,
            "strategy_id": canonical_id(d.strategy_id, alias_map),
            "ticker": s.ticker_received,
            "action": s.action,
            "outcome": d.outcome,
            "score": d.score,
            "block_reason": d.block_reason,
        }
        for d, s in recent.all()
    ]

    total_dec = sum(outcomes.values())
    approve_n = outcomes.get("APPROVE", 0)
    ctx = {
        "days": days,
        "rangos": RANGOS_DIAS,
        "summary": {
            "received": received,
            "total": total_dec,
            "approved": approve_n,
            "blocked": outcomes.get("BLOCK", 0),
            "approval_rate": (round(100 * approve_n / total_dec, 1)
                              if total_dec else 0.0),
            "sent": delivery_status.get("SENT", 0),
            "failed": delivery_status.get("FAILED", 0),
        },
        "chart_data": {
            "outcomes": outcomes,
            "block_reasons": block_reasons,
            "blocks_by_level": blocks_by_level,
            "timeseries": timeseries,
        },
        "by_strategy": by_strategy,
        "strategy_counts": strategy_counts,
        "recent_decisions": recent_decisions,
        "messages": flash_messages(request),
    }
    return await render(request, "dashboard.html", ctx, db=db)


@router.get("/ui/analytics")
async def analytics_redirect(days: int = 14) -> RedirectResponse:
    """P2 — Analítica se fusionó con el Dashboard (una página, selector de
    rango). Redirect permanente: bookmarks y links viejos siguen vivos;
    sin days explícito conserva la ventana por defecto que tenía (14d)."""
    return RedirectResponse(f"/ui?days={days}", status_code=301)


# ---------------------------------------------------------------------------
# HTMX partials
# ---------------------------------------------------------------------------

async def _bridge_rows(db: AsyncSession) -> list[dict]:
    """Una fila por símbolo de DATOS (el bridge sirve un feed por dato: micro y
    padre comparten archivos), con badge de los tradeables que respalda. Colapsa
    las filas duplicadas (16 tradeables → 8 feeds) y adjunta tick_size del Symbol
    Mapper (fuente única) para expresar los ATR de FX en ticks, no en '0.00'."""
    from app.models.symbol_map import SymbolMap

    statuses = (await db.execute(
        select(MarketDataStatus).order_by(MarketDataStatus.symbol)
    )).scalars().all()
    sm_by_tv = {
        sm.tv_symbol: sm
        for sm in (await db.execute(select(SymbolMap))).scalars().all()
    }

    groups: dict[str, dict] = {}
    for st in statuses:
        sm = sm_by_tv.get(st.symbol)
        # DATO = market_data_symbol si el tradeable lo redirige (MES → ES),
        # si no el propio símbolo (padres se mapean a sí mismos).
        data_symbol = (sm.market_data_symbol if sm and sm.market_data_symbol
                       else st.symbol)
        tick_size = (float(sm.tick_size)
                     if sm and sm.tick_size is not None else None)

        g = groups.get(data_symbol)
        if g is None:
            g = groups[data_symbol] = {
                "symbol": data_symbol,
                "active": st.is_active,
                "atr_5m": st.last_atr_5m,
                "atr_1h": st.last_atr_1h,
                "heartbeat_age": st.heartbeat_age_seconds,
                "tick_size": tick_size,
                "tradeables": [],
            }
        else:
            # Mismo feed: conserva señal viva y el primer dato no nulo (micro y
            # padre comparten probe → valores idénticos, esto es defensivo).
            g["active"] = g["active"] or st.is_active
            if g["atr_5m"] is None:
                g["atr_5m"] = st.last_atr_5m
            if g["atr_1h"] is None:
                g["atr_1h"] = st.last_atr_1h
            if g["heartbeat_age"] is None:
                g["heartbeat_age"] = st.heartbeat_age_seconds
            if g["tick_size"] is None:
                g["tick_size"] = tick_size
        # Tradeable respaldado = símbolo operado distinto del dato (ES → MES).
        if st.symbol != data_symbol:
            g["tradeables"].append(st.symbol)

    rows: list[dict] = []
    for ds in sorted(groups):
        g = groups[ds]
        g["tradeables"] = sorted(set(g["tradeables"]))
        rows.append(g)
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


@router.get("/ui/partials/delivery-alerts", response_class=HTMLResponse)
async def delivery_alerts_partial(
    request: Request, db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    """Fase 2 — red banner for FAILED TradersPost deliveries (last 24h)."""
    from app.web.common import templates
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    count = await _count(
        db,
        select(func.count(WebhookDelivery.id)).where(
            WebhookDelivery.status == "FAILED",
            WebhookDelivery.created_at >= since,
        ),
    )
    result = await db.execute(
        select(WebhookDelivery)
        .where(WebhookDelivery.status == "FAILED",
               WebhookDelivery.created_at >= since)
        .order_by(WebhookDelivery.created_at.desc())
        .limit(5)
    )
    rows = []
    for d in result.scalars().all():
        detail = d.error_message or (
            f"HTTP {d.response_status_code}" if d.response_status_code else "error")
        rows.append({"time": d.created_at, "strategy_id": d.strategy_id,
                     "detail": detail})
    return templates.TemplateResponse(
        request, "partials/delivery_alerts.html", {"count": count, "rows": rows}
    )
