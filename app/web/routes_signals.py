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


# ─────────────────────────────────────────────────────────────────────────────
# Filter-ribbon builder — maps each Config-tab layer to a node with a state read
# from pipeline_execution_json (what actually happened) + the effective config
# (so layers that are OFF in config render as "inactivo", distinct from layers
# that were "no evaluado" because an earlier level blocked first).
# States: pass · block · skip · inactive · na
# ─────────────────────────────────────────────────────────────────────────────
def _node(key, label, state, summary):
    return {"key": key, "label": label, "state": state, "summary": summary}


def build_ribbon(decision, pipeline: dict, cfg: dict, deliveries: list) -> list[dict]:
    p = pipeline or {}
    cfg = cfg or {}
    l1 = p.get("level_1") or {}
    stale = p.get("staleness") or {}
    l2 = p.get("level_2") or {}
    l3 = p.get("level_3") or {}
    rg = p.get("regime") or {}
    l4 = p.get("level_4") or {}
    l5 = p.get("level_5") or {}
    is_exit = bool(l3.get("skipped") or l4.get("skipped") or l5.get("skipped"))
    nodes: list[dict] = []

    # ① Sistema (Nivel 1)
    if l1.get("outcome") and l1.get("outcome") != "CONTINUE":
        nodes.append(_node("sistema", "Sistema", "block", f"Bloqueado: {l1.get('reason')}"))
    elif l1:
        nodes.append(_node("sistema", "Sistema", "pass", "Modo/estado/mapeo/bridge OK"))
    else:
        nodes.append(_node("sistema", "Sistema", "na", "No evaluado"))

    # ② Rango de operación (Nivel 2 + frescura)
    if stale.get("failed"):
        nodes.append(_node("rango", "Rango op.", "block", f"Señal vieja: {stale.get('reason')}"))
    elif l2.get("failed"):
        nodes.append(_node("rango", "Rango op.", "block", f"Fuera de horario: {l2.get('reason')}"))
    elif l2 or stale:
        nodes.append(_node("rango", "Rango op.", "pass", "Dentro de la ventana de sesión"))
    else:
        nodes.append(_node("rango", "Rango op.", "na", "No evaluado"))

    # ③ Filtro técnico (QualityScorer — Nivel 4)
    if l4.get("skipped"):
        nodes.append(_node("filtro", "Filtro técnico", "skip", "Exento (salida)"))
    elif l4.get("score") is not None:
        smin = cfg.get("score_minimum", 70)
        if l4.get("passed"):
            nodes.append(_node("filtro", "Filtro técnico", "pass", f"Score {l4.get('score')} ≥ {smin}"))
        else:
            nodes.append(_node("filtro", "Filtro técnico", "block", f"Score {l4.get('score')} < {smin}"))
    else:
        nodes.append(_node("filtro", "Filtro técnico", "na", "No evaluado"))

    # ④ Régimen (HMM)
    regime_on = bool((cfg.get("regime") or {}).get("enabled"))
    if not regime_on:
        nodes.append(_node("regimen", "Régimen HMM", "inactive", "Desactivado en config"))
    elif rg:
        allowed = rg.get("allowed") or []
        reg = rg.get("regime")
        if allowed and reg not in allowed and reg != "unknown":
            nodes.append(_node("regimen", "Régimen HMM", "block", f"{reg} no permitido"))
        else:
            nodes.append(_node("regimen", "Régimen HMM", "pass", f"{reg} @ {rg.get('timeframe')}"))
    elif is_exit:
        nodes.append(_node("regimen", "Régimen HMM", "skip", "Exento (salida)"))
    else:
        nodes.append(_node("regimen", "Régimen HMM", "na", "No evaluado"))

    # ⑤ SL por ATR (Nivel 5)
    if l5.get("skipped"):
        nodes.append(_node("sl", "SL por ATR", "skip", "Exento (salida — sin SL)"))
    elif l5.get("passed"):
        nodes.append(_node("sl", "SL por ATR", "pass", f"SL {l5.get('sl_price')}"))
    elif l5.get("reason"):
        nodes.append(_node("sl", "SL por ATR", "block", f"Bloqueado: {l5.get('reason')}"))
    else:
        nodes.append(_node("sl", "SL por ATR", "na", "No evaluado"))

    # ⑥ TP
    tp_mult = cfg.get("tp_atr_multiplier")
    if not tp_mult:
        nodes.append(_node("tp", "TP", "inactive", "TP por Builtin-Exits (LuxAlgo)"))
    elif l5.get("skipped"):
        nodes.append(_node("tp", "TP", "skip", "Exento (salida)"))
    elif l5.get("passed"):
        tpp = l5.get("tp_price")
        nodes.append(_node("tp", "TP", "pass", f"TP {tpp}" if tpp else f"k={tp_mult}×ATR"))
    else:
        nodes.append(_node("tp", "TP", "na", "No evaluado"))

    # ⑦ Compras escalonadas
    se = cfg.get("scale_entry") or {}
    mode = (se.get("mode") or "off").lower()
    n_legs = len(deliveries)
    if mode not in ("execute", "live"):
        nodes.append(_node("escalonada", "Escalonada", "inactive", f"Modo: {mode}"))
    elif n_legs > 1:
        nodes.append(_node("escalonada", "Escalonada", "pass", f"{n_legs} piernas enviadas"))
    elif decision.outcome == "APPROVE" and n_legs == 1:
        nodes.append(_node("escalonada", "Escalonada", "pass", "1 pierna (fallback)"))
    elif decision.outcome == "APPROVE":
        nodes.append(_node("escalonada", "Escalonada", "na", "Aprobada sin envío"))
    else:
        nodes.append(_node("escalonada", "Escalonada", "na", "No evaluado"))

    # ⑧ Decisión final
    if decision.outcome == "APPROVE":
        nodes.append(_node("decision", "Decisión", "pass", "APPROVE"))
    else:
        nodes.append(_node("decision", "Decisión", "block", decision.outcome))

    return nodes


def scale_plan(cfg: dict) -> list[dict]:
    """Planned legs from config (independent of what was sent): C1 market + limits."""
    se = (cfg or {}).get("scale_entry") or {}
    quantities = [int(q or 0) for q in (se.get("quantities") or [])]
    levels = [float(x) for x in (se.get("levels") or [])]
    plan: list[dict] = []
    for i, q in enumerate(quantities):
        plan.append({
            "leg": i + 1,
            "type": "MERCADO" if i == 0 else "LÍMITE",
            "offset": 0.0 if i == 0 else (levels[i - 1] if (i - 1) < len(levels) else None),
            "qty": q,
        })
    return plan


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

    # Scaled entries produce ONE WebhookDelivery per leg (C1 market + C2/C3
    # limit), so there can be several rows. Fetch them all, ordered, and expose
    # each leg's payload (the exact JSON sent to TradersPost).
    deliv_res = await db.execute(
        select(WebhookDelivery)
        .where(WebhookDelivery.decision_id == decision.id)
        .order_by(WebhookDelivery.created_at.asc())
    )
    deliveries = list(deliv_res.scalars().all())

    # Effective config of the strategy → lets the ribbon show layers that are
    # OFF in config (regime / TP / scale) distinct from "not evaluated".
    cfg: dict = {}
    try:
        from app.services.config_resolver import ConfigResolver
        from app.services.repositories import get_strategy_by_id

        strategy = await get_strategy_by_id(db, decision.strategy_id)
        if strategy is not None:
            cfg = await ConfigResolver().resolve(
                db, decision.strategy_id, strategy.asset_symbol
            )
    except Exception:  # config is best-effort decoration; never break the page
        cfg = {}

    pipeline = decision.pipeline_execution_json or {}
    ribbon = build_ribbon(decision, pipeline, cfg, deliveries)

    return await render(
        request, "signal_detail.html",
        {
            "decision": decision, "signal": signal,
            "raw": raw,
            "deliveries": deliveries,
            "delivery": deliveries[0] if deliveries else None,
            "pipeline": pipeline,
            "ribbon": ribbon,
            "scale_plan": scale_plan(cfg),
            "cfg": {
                "score_minimum": cfg.get("score_minimum"),
                "sl_atr_multiplier": cfg.get("sl_atr_multiplier"),
                "tp_atr_multiplier": cfg.get("tp_atr_multiplier"),
                "scale_mode": (cfg.get("scale_entry") or {}).get("mode", "off"),
            },
        }, db=db,
    )
