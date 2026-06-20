"""Strategy management UI routes."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.asset_profile import AssetProfile
from app.models.decision import StrategyDecision
from app.models.normalized_signal import NormalizedSignal
from app.models.strategy import Strategy
from app.models.strategy_performance import StrategyPerformance
from app.models.strategy_profile import StrategyProfile
from app.models.strategy_template import StrategyTemplate
from app.services.audit_service import AuditService
from app.web.common import render, redirect, flash_messages, templates

router = APIRouter()

_VALID_STATUSES = {
    "candidate", "shadow", "paper", "micro", "limited_live", "live",
    "paused", "quarantined", "retired",
}


async def _assets(db: AsyncSession) -> list[AssetProfile]:
    result = await db.execute(
        select(AssetProfile).where(AssetProfile.active.is_(True)).order_by(AssetProfile.symbol)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

@router.get("/ui/strategies", response_class=HTMLResponse)
async def list_strategies(
    request: Request, db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    result = await db.execute(select(Strategy).order_by(Strategy.created_at.desc()))
    strategies = list(result.scalars().all())

    # signals-today count per strategy
    perf_rows = await db.execute(select(StrategyPerformance))
    perf = {p.strategy_id: p for p in perf_rows.scalars().all()}

    items = []
    for s in strategies:
        p = perf.get(s.strategy_id)
        items.append({
            "strategy_id": s.strategy_id,
            "name": s.name,
            "asset_symbol": s.asset_symbol,
            "timeframe": s.timeframe,
            "status": s.status,
            "enabled": s.enabled,
            "pass_rate": float(p.filter_pass_rate) if p and p.filter_pass_rate else None,
            "total_received": p.total_signals_received if p else 0,
        })

    return await render(
        request, "strategies.html",
        {"strategies": items, "messages": flash_messages(request)}, db=db,
    )


# ---------------------------------------------------------------------------
# New
# ---------------------------------------------------------------------------

@router.get("/ui/strategies/ticker-hint", response_class=HTMLResponse)
async def ticker_hint(
    request: Request, asset_symbol: str = "", db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    """HTMX partial: show the exact pine_script_config for the chosen asset."""
    pine = None
    if asset_symbol:
        res = await db.execute(
            select(AssetProfile).where(AssetProfile.symbol == asset_symbol)
        )
        ap = res.scalar_one_or_none()
        pine = ap.pine_script_config if ap else None
    return templates.TemplateResponse(
        request, "partials/ticker_hint.html",
        {"pine": pine, "asset_symbol": asset_symbol},
    )


@router.get("/ui/strategies/new", response_class=HTMLResponse)
async def new_strategy_form(
    request: Request, db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    tpl_rows = await db.execute(select(StrategyTemplate))
    templates_list = list(tpl_rows.scalars().all())
    return await render(
        request, "strategy_form.html",
        {"assets": await _assets(db), "templates_list": templates_list}, db=db,
    )


@router.post("/ui/strategies/new")
async def create_strategy_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    strategy_id: str = Form(...),
    name: str = Form(...),
    asset_symbol: str = Form(""),
    timeframe: str = Form("5m"),
    sl_atr_multiplier: str = Form(""),
    score_minimum: str = Form(""),
    traderspost_webhook_url: str = Form(""),
    initial_mode: str = Form("paper"),
    enforce_symbol_match: str = Form(""),
    enforce_timeframe_match: str = Form(""),
    signal_max_age_entry_seconds: str = Form(""),
    signal_max_age_exit_seconds: str = Form(""),
) -> RedirectResponse:
    # Reject duplicate strategy_id
    existing = await db.execute(
        select(Strategy).where(Strategy.strategy_id == strategy_id)
    )
    if existing.scalar_one_or_none() is not None:
        return redirect(
            "/ui/strategies/new",
            flash=f"strategy_id '{strategy_id}' ya existe", category="error",
        )

    strategy = Strategy(
        strategy_id=strategy_id,
        name=name,
        asset_symbol=asset_symbol or None,
        timeframe=timeframe or None,
        status="candidate",
        enabled=False,
        traderspost_webhook_url=traderspost_webhook_url or None,
    )
    db.add(strategy)

    # Strategy profile with overrides
    profile = StrategyProfile(
        strategy_id=strategy_id,
        mode=initial_mode if initial_mode in ("paper", "micro", "limited_live", "live") else "paper",
        traderspost_webhook_url=traderspost_webhook_url or None,
    )
    if sl_atr_multiplier:
        try:
            profile.sl_atr_multiplier = float(sl_atr_multiplier)
        except ValueError:
            pass

    # Anexo 08 #2 — per-strategy guardrails stored in pipeline_config_json.
    guardrails: dict = {}
    if enforce_symbol_match:
        guardrails["enforce_symbol_match"] = True
    if enforce_timeframe_match:
        guardrails["enforce_timeframe_match"] = True
    for _field, _key in (
        (signal_max_age_entry_seconds, "signal_max_age_entry_seconds"),
        (signal_max_age_exit_seconds, "signal_max_age_exit_seconds"),
    ):
        if _field.strip():
            try:
                guardrails[_key] = int(_field)
            except ValueError:
                pass
    if guardrails:
        profile.pipeline_config_json = {"guardrails": guardrails}

    db.add(profile)

    await AuditService().log(
        db, actor="admin", action="CREATE", object_type="Strategy",
        object_id=strategy_id, new_value={"name": name, "asset": asset_symbol},
        reason="created via UI",
    )
    await db.commit()
    return redirect(
        f"/ui/strategies/{strategy_id}",
        flash=f"Estrategia '{strategy_id}' creada en estado CANDIDATE",
    )


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------

@router.get("/ui/strategies/{strategy_id}", response_class=HTMLResponse)
async def strategy_detail(
    request: Request, strategy_id: str, db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    result = await db.execute(
        select(Strategy).where(Strategy.strategy_id == strategy_id)
    )
    strategy = result.scalar_one_or_none()
    if strategy is None:
        return redirect("/ui/strategies", flash="Estrategia no encontrada", category="error")

    prof_res = await db.execute(
        select(StrategyProfile).where(StrategyProfile.strategy_id == strategy_id)
    )
    profile = prof_res.scalar_one_or_none()

    perf_res = await db.execute(
        select(StrategyPerformance).where(StrategyPerformance.strategy_id == strategy_id)
    )
    perf = perf_res.scalar_one_or_none()

    dec_res = await db.execute(
        select(StrategyDecision, NormalizedSignal)
        .join(NormalizedSignal, StrategyDecision.normalized_signal_id == NormalizedSignal.id)
        .where(StrategyDecision.strategy_id == strategy_id)
        .order_by(StrategyDecision.created_at.desc())
        .limit(10)
    )
    decisions = [
        {
            "id": d.id, "time": d.created_at, "outcome": d.outcome,
            "ticker": s.ticker_received, "action": s.action,
            "score": d.score, "block_reason": d.block_reason, "block_level": d.block_level,
        }
        for d, s in dec_res.all()
    ]

    return await render(
        request, "strategy_detail.html",
        {
            "strategy": strategy, "profile": profile, "perf": perf,
            "decisions": decisions, "messages": flash_messages(request),
        }, db=db,
    )


@router.post("/ui/strategies/{strategy_id}/status")
async def change_status(
    request: Request,
    strategy_id: str,
    db: AsyncSession = Depends(get_db),
    new_status: str = Form(...),
    reason: str = Form(""),
) -> RedirectResponse:
    if new_status not in _VALID_STATUSES:
        return redirect(
            f"/ui/strategies/{strategy_id}",
            flash=f"Status inválido: {new_status}", category="error",
        )

    result = await db.execute(
        select(Strategy).where(Strategy.strategy_id == strategy_id)
    )
    strategy = result.scalar_one_or_none()
    if strategy is None:
        return redirect("/ui/strategies", flash="Estrategia no encontrada", category="error")

    # quarantine/retire require a reason
    if new_status in ("quarantined", "retired") and not reason.strip():
        return redirect(
            f"/ui/strategies/{strategy_id}",
            flash=f"'{new_status}' requiere un motivo", category="error",
        )

    old_status = strategy.status
    strategy.status = new_status
    # enabled follows execution-capable statuses
    strategy.enabled = new_status in ("shadow", "paper", "micro", "limited_live", "live")
    if new_status == "retired":
        strategy.retired_at = datetime.now(timezone.utc)
        strategy.retired_reason = reason or None

    await AuditService().log_strategy_change(
        db, actor="admin", strategy_id=strategy_id,
        old_data={"status": old_status}, new_data={"status": new_status},
        action="STATUS_CHANGE", reason=reason or None,
    )
    await db.commit()
    return redirect(
        f"/ui/strategies/{strategy_id}",
        flash=f"Status: {old_status} → {new_status}",
    )


# ---------------------------------------------------------------------------
# Clone
# ---------------------------------------------------------------------------

@router.get("/ui/strategies/{strategy_id}/clone", response_class=HTMLResponse)
async def clone_form(
    request: Request, strategy_id: str, db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    result = await db.execute(
        select(Strategy).where(Strategy.strategy_id == strategy_id)
    )
    source = result.scalar_one_or_none()
    if source is None:
        return redirect("/ui/strategies", flash="Estrategia no encontrada", category="error")
    return await render(
        request, "strategy_clone_form.html",
        {"source": source, "assets": await _assets(db)}, db=db,
    )


@router.post("/ui/strategies/{strategy_id}/clone")
async def clone_strategy(
    request: Request,
    strategy_id: str,
    db: AsyncSession = Depends(get_db),
    new_strategy_id: str = Form(...),
    asset_symbol: str = Form(""),
    traderspost_webhook_url: str = Form(""),
) -> RedirectResponse:
    src_res = await db.execute(
        select(Strategy).where(Strategy.strategy_id == strategy_id)
    )
    source = src_res.scalar_one_or_none()
    if source is None:
        return redirect("/ui/strategies", flash="Fuente no encontrada", category="error")

    dup = await db.execute(
        select(Strategy).where(Strategy.strategy_id == new_strategy_id)
    )
    if dup.scalar_one_or_none() is not None:
        return redirect(
            f"/ui/strategies/{strategy_id}/clone",
            flash=f"strategy_id '{new_strategy_id}' ya existe", category="error",
        )

    clone = Strategy(
        strategy_id=new_strategy_id,
        name=f"{source.name} (clon)",
        source=source.source,
        asset_symbol=asset_symbol or source.asset_symbol,
        timeframe=source.timeframe,
        strategy_type=source.strategy_type,
        status="candidate",  # clones always start in candidate
        enabled=False,
        traderspost_webhook_url=traderspost_webhook_url or None,
        template_id=source.template_id,
    )
    db.add(clone)

    # Clone the strategy profile config
    src_prof = await db.execute(
        select(StrategyProfile).where(StrategyProfile.strategy_id == strategy_id)
    )
    sp = src_prof.scalar_one_or_none()
    if sp is not None:
        db.add(StrategyProfile(
            strategy_id=new_strategy_id,
            mode=sp.mode,
            sl_atr_multiplier=sp.sl_atr_multiplier,
            tp_atr_multiplier=sp.tp_atr_multiplier,
            atr_period=sp.atr_period,
            atr_timeframe=sp.atr_timeframe,
            traderspost_webhook_url=traderspost_webhook_url or None,
        ))

    await AuditService().log(
        db, actor="admin", action="CLONE", object_type="Strategy",
        object_id=new_strategy_id,
        new_value={"cloned_from": strategy_id},
        reason=f"cloned from {strategy_id}",
    )
    await db.commit()
    return redirect(
        f"/ui/strategies/{new_strategy_id}",
        flash=f"Clonada desde '{strategy_id}' → '{new_strategy_id}'",
    )


# ---------------------------------------------------------------------------
# Batch action
# ---------------------------------------------------------------------------

@router.post("/ui/strategies/batch-action")
async def batch_action(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    form = await request.form()
    action = form.get("action", "")
    selected = form.getlist("selected")

    action_map = {
        "pause": "paused", "resume": "paper",
        "shadow": "shadow", "quarantine": "quarantined", "retire": "retired",
    }
    new_status = action_map.get(action)
    if not new_status or not selected:
        return redirect("/ui/strategies", flash="Acción o selección inválida", category="error")

    audit = AuditService()
    count = 0
    for sid in selected:
        res = await db.execute(select(Strategy).where(Strategy.strategy_id == sid))
        strat = res.scalar_one_or_none()
        if strat is None:
            continue
        old = strat.status
        strat.status = new_status
        strat.enabled = new_status in ("shadow", "paper", "micro", "limited_live", "live")
        await audit.log_strategy_change(
            db, actor="admin", strategy_id=sid,
            old_data={"status": old}, new_data={"status": new_status},
            action="STATUS_CHANGE", reason=f"batch {action}",
        )
        count += 1

    await db.commit()
    return redirect("/ui/strategies", flash=f"{count} estrategia(s) → {new_status}")
