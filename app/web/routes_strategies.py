"""Strategy management UI routes."""
from __future__ import annotations

import json
import secrets

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
from app.models.symbol_map import SymbolMap
from app.services.audit_service import AuditService
from app.core.config import settings as app_settings
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
    sm_info = None
    if asset_symbol:
        res = await db.execute(
            select(AssetProfile).where(AssetProfile.symbol == asset_symbol)
        )
        ap = res.scalar_one_or_none()
        pine = ap.pine_script_config if ap else None
        # Instrument catalog (Anexo 08 #4) — reference data for the operator.
        smres = await db.execute(
            select(SymbolMap).where(SymbolMap.tv_symbol == asset_symbol)
        )
        sm = smres.scalar_one_or_none()
        if sm is not None and sm.tick_value is not None:
            sm_info = {
                "tick_value": float(sm.tick_value),
                "tick_size": float(sm.tick_size) if sm.tick_size is not None else None,
                "contract_type": sm.contract_type,
            }
    return templates.TemplateResponse(
        request, "partials/ticker_hint.html",
        {"pine": pine, "asset_symbol": asset_symbol, "sm_info": sm_info},
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
        # Anexo 08 — per-strategy webhook token for the LuxAlgo URL.
        webhook_token=secrets.token_urlsafe(24),
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

    # Full registration ficha (machote). Extra fields read from the raw form to
    # avoid an enormous handler signature.
    form = await request.form()

    def _s(key: str) -> str | None:
        v = (form.get(key) or "").strip()
        return v or None

    def _num(key, cast):
        v = (form.get(key) or "").strip()
        if not v:
            return None
        try:
            return cast(v)
        except (ValueError, TypeError):
            return None

    # Identity extras + definition/backtest → Strategy.notes / luxalgo_metrics_json
    strategy.notes = _s("descripcion")
    metrics: dict = {}
    for _k in ("responsable", "toolkit", "trigger", "filter_1", "filter_2",
               "exit_condition", "frequency", "order_size"):
        _v = _s(_k)
        if _v:
            metrics[_k] = _v
    bt: dict = {}
    for _k in ("bt_start", "bt_end"):
        _v = _s(_k)
        if _v:
            bt[_k] = _v
    for _k, _cast in (("num_trades", int), ("winrate", float),
                      ("profit_factor", float), ("net_profit", float),
                      ("max_drawdown", float)):
        _v = _num(_k, _cast)
        if _v is not None:
            bt[_k] = _v
    if bt:
        metrics["backtest"] = bt
    if metrics:
        strategy.luxalgo_metrics_json = metrics

    # Profile pipeline_config_json: guardrails + reference-only sections.
    cfg: dict = {}
    if guardrails:
        cfg["guardrails"] = guardrails
    risk_ref: dict = {}
    if form.get("stop_required"):
        risk_ref["stop_required"] = True
    for _k, _cast in (("stop_ticks", int), ("risk_usd_max_operation", float),
                      ("max_contracts", int)):
        _v = _num(_k, _cast)
        if _v is not None:
            risk_ref[_k] = _v
    if risk_ref:
        cfg["risk_reference"] = risk_ref  # documentation only; NOT enforced
    _dedup = _num("dedup_seconds", int)
    if _dedup is not None:
        cfg["dedup_seconds"] = _dedup
    _conf = _s("confirmaciones")
    if _conf:
        cfg["confirmaciones"] = _conf
    routing: dict = {}
    if _s("target_account"):
        routing["target_account"] = _s("target_account")
    if _s("routing_notes"):
        routing["notes"] = _s("routing_notes")
    if routing:
        cfg["routing"] = routing
    if cfg:
        profile.pipeline_config_json = cfg

    # Section 4 scalars: exits-always + forced EOD close.
    if form.get("allow_exits_outside_window"):
        profile.allow_exits_outside_window = True
    _eod = _s("force_flat_time")
    if _eod:
        from datetime import time as _time
        try:
            _hh, _mm = _eod.split(":")[:2]
            profile.force_flat_time = _time(int(_hh), int(_mm))
        except (ValueError, IndexError):
            pass

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

    base = str(request.base_url).rstrip("/")
    webhook_url = (
        f"{base}/webhooks/luxalgo/{strategy.strategy_id}?token={strategy.webhook_token}"
        if strategy.webhook_token else None
    )

    return await render(
        request, "strategy_detail.html",
        {
            "strategy": strategy, "profile": profile, "perf": perf,
            "decisions": decisions, "webhook_url": webhook_url,
            "tp_env_enabled": app_settings.TRADERSPOST_ENABLED,
            "messages": flash_messages(request),
        }, db=db,
    )


@router.post("/ui/strategies/{strategy_id}/dispatch")
async def update_dispatch(
    request: Request,
    strategy_id: str,
    db: AsyncSession = Depends(get_db),
    action: str = Form(...),
    confirm: str = Form(""),
) -> RedirectResponse:
    """Fase 2 — arm/disarm real dispatch for ONE strategy (CONFIRMAR to arm).

    arm    → traderspost_enabled=True, dry_run=False (requires confirm==CONFIRMAR)
    disarm → dry_run=True (safe direction, no confirmation)
    Real send still also requires the global profile and the env kill-switch.
    """
    prof_res = await db.execute(
        select(StrategyProfile).where(StrategyProfile.strategy_id == strategy_id)
    )
    profile = prof_res.scalar_one_or_none()
    if profile is None:
        profile = StrategyProfile(strategy_id=strategy_id)
        db.add(profile)

    if action == "arm":
        if confirm != "CONFIRMAR":
            return redirect(
                f"/ui/strategies/{strategy_id}",
                flash="Escribe CONFIRMAR para armar el envío real", category="error")
        profile.traderspost_enabled = True
        profile.dry_run = False
        msg = "Envío real ARMADO (sujeto al global y al kill-switch del servidor)"
    elif action == "disarm":
        profile.dry_run = True
        msg = "Estrategia de vuelta en DRY_RUN"
    else:
        return redirect(f"/ui/strategies/{strategy_id}",
                        flash="Acción inválida", category="error")

    await AuditService().log(
        db, actor="admin", action="DISPATCH_CHANGE", object_type="StrategyProfile",
        object_id=strategy_id,
        new_value={"action": action, "traderspost_enabled": profile.traderspost_enabled,
                   "dry_run": profile.dry_run},
        reason="dispatch toggled via UI")
    await db.commit()
    return redirect(f"/ui/strategies/{strategy_id}", flash=msg)


@router.post("/ui/strategies/{strategy_id}/regenerate-token")
async def regenerate_token(
    request: Request,
    strategy_id: str,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Anexo 08 — (re)generate the per-strategy webhook token for LuxAlgo."""
    result = await db.execute(
        select(Strategy).where(Strategy.strategy_id == strategy_id)
    )
    strategy = result.scalar_one_or_none()
    if strategy is None:
        return redirect("/ui/strategies", flash="Estrategia no encontrada",
                        category="error")
    strategy.webhook_token = secrets.token_urlsafe(24)
    await AuditService().log(
        db, actor="admin", action="UPDATE", object_type="Strategy",
        object_id=strategy_id, reason="webhook token regenerated via UI",
    )
    await db.commit()
    return redirect(
        f"/ui/strategies/{strategy_id}",
        flash="Token de webhook regenerado — actualiza la URL en LuxAlgo",
    )


@router.post("/ui/strategies/{strategy_id}/guardrails")
async def update_guardrails(
    request: Request,
    strategy_id: str,
    db: AsyncSession = Depends(get_db),
    enforce_symbol_match: str = Form(""),
    enforce_timeframe_match: str = Form(""),
    signal_max_age_entry_seconds: str = Form(""),
    signal_max_age_exit_seconds: str = Form(""),
) -> RedirectResponse:
    """Anexo 08 #2 — edit the per-strategy guardrails on the detail page."""
    prof_res = await db.execute(
        select(StrategyProfile).where(StrategyProfile.strategy_id == strategy_id)
    )
    profile = prof_res.scalar_one_or_none()
    if profile is None:
        profile = StrategyProfile(strategy_id=strategy_id)
        db.add(profile)

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

    # Preserve any other pipeline_config_json keys; replace only "guardrails".
    cfg = dict(profile.pipeline_config_json or {})
    if guardrails:
        cfg["guardrails"] = guardrails
    else:
        cfg.pop("guardrails", None)
    profile.pipeline_config_json = cfg or None

    await AuditService().log(
        db, actor="admin", action="UPDATE", object_type="StrategyProfile",
        object_id=strategy_id, new_value={"guardrails": guardrails},
        reason="guardrails updated via UI",
    )
    await db.commit()
    return redirect(
        f"/ui/strategies/{strategy_id}",
        flash="Guardarraíles actualizados",
    )


@router.post("/ui/strategies/{strategy_id}/windows")
async def update_windows(
    request: Request,
    strategy_id: str,
    db: AsyncSession = Depends(get_db),
    windows_json: str = Form(""),
) -> RedirectResponse:
    """Anexo 08 #5 — save repeatable operation windows (days per window)."""
    prof_res = await db.execute(
        select(StrategyProfile).where(StrategyProfile.strategy_id == strategy_id)
    )
    profile = prof_res.scalar_one_or_none()
    if profile is None:
        profile = StrategyProfile(strategy_id=strategy_id)
        db.add(profile)

    try:
        raw = json.loads(windows_json or "[]")
    except (ValueError, TypeError):
        raw = []

    clean: list = []
    if isinstance(raw, list):
        for w in raw:
            if not isinstance(w, dict):
                continue
            start, end = w.get("start"), w.get("end")
            days = w.get("days")
            if not isinstance(days, list) or not start or not end:
                continue
            days_i = sorted({
                int(d) for d in days
                if (isinstance(d, (int, float))
                    or (isinstance(d, str) and d.isdigit()))
                and 0 <= int(d) <= 6
            })
            if not days_i:
                continue
            item: dict = {"days": days_i, "start": str(start), "end": str(end)}
            if w.get("next_day_end"):
                item["next_day_end"] = True
            clean.append(item)

    cfg = dict(profile.pipeline_config_json or {})
    if clean:
        cfg["windows"] = clean
    else:
        cfg.pop("windows", None)
    profile.pipeline_config_json = cfg or None

    await AuditService().log(
        db, actor="admin", action="UPDATE", object_type="StrategyProfile",
        object_id=strategy_id, new_value={"windows": clean},
        reason="windows updated via UI",
    )
    await db.commit()
    return redirect(
        f"/ui/strategies/{strategy_id}",
        flash=f"{len(clean)} ventana(s) guardada(s)",
    )


@router.post("/ui/strategies/{strategy_id}/filters")
async def update_filters(
    request: Request,
    strategy_id: str,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Fase 5 — edit the Level-4 QualityScorer filters (enabled + weight).

    Stored in pipeline_config_json["filters"] as {name: {enabled, weight}}.
    If no filter is enabled (with weight > 0) the key is removed, so the scorer
    returns 100 (pass-through). Weights are preserved while any filter is active.
    """
    prof_res = await db.execute(
        select(StrategyProfile).where(StrategyProfile.strategy_id == strategy_id)
    )
    profile = prof_res.scalar_one_or_none()
    if profile is None:
        profile = StrategyProfile(strategy_id=strategy_id)
        db.add(profile)

    form = await request.form()
    filters: dict = {}
    any_enabled = False
    for name in ("volume_relative", "atr_normalized", "vwap_position", "time_of_day"):
        enabled = bool(form.get(f"f_{name}_enabled"))
        raw_w = (form.get(f"f_{name}_weight") or "").strip()
        try:
            weight = float(raw_w) if raw_w else 1.0
        except (ValueError, TypeError):
            weight = 1.0
        if weight < 0:
            weight = 0.0
        filters[name] = {"enabled": enabled, "weight": weight}
        if enabled and weight > 0:
            any_enabled = True

    # Merge: replace only the "filters" key, preserving guardrails/windows/etc.
    cfg = dict(profile.pipeline_config_json or {})
    if any_enabled:
        cfg["filters"] = filters
    else:
        cfg.pop("filters", None)
    profile.pipeline_config_json = cfg or None

    await AuditService().log(
        db, actor="admin", action="UPDATE", object_type="StrategyProfile",
        object_id=strategy_id,
        new_value={"filters": filters if any_enabled else {}},
        reason="quality filters updated via UI",
    )
    await db.commit()
    return redirect(
        f"/ui/strategies/{strategy_id}",
        flash="Filtros de calidad actualizados" if any_enabled
        else "Filtros de calidad desactivados (score 100, pasa-directo)",
    )


@router.post("/ui/strategies/{strategy_id}/regime")
async def update_regime(
    request: Request,
    strategy_id: str,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Fase 6 — edit the Level-4 market-regime gate (opt-in).

    Stored in pipeline_config_json["regime"] as
    {enabled, timeframe, allowed_regimes}. Stored only when enabled AND at
    least one regime is allowed; otherwise the key is removed (gate disabled).
    """
    prof_res = await db.execute(
        select(StrategyProfile).where(StrategyProfile.strategy_id == strategy_id)
    )
    profile = prof_res.scalar_one_or_none()
    if profile is None:
        profile = StrategyProfile(strategy_id=strategy_id)
        db.add(profile)

    form = await request.form()
    enabled = bool(form.get("regime_enabled"))
    timeframe = (form.get("regime_timeframe") or "1h").strip()
    if timeframe not in ("1h", "4h"):
        timeframe = "1h"
    allowed = [
        r for r in ("trending_bull", "trending_bear", "ranging")
        if form.get(f"regime_allow_{r}")
    ]

    cfg = dict(profile.pipeline_config_json or {})
    if enabled and allowed:
        cfg["regime"] = {
            "enabled": True, "timeframe": timeframe, "allowed_regimes": allowed,
        }
    else:
        cfg.pop("regime", None)
    profile.pipeline_config_json = cfg or None

    await AuditService().log(
        db, actor="admin", action="UPDATE", object_type="StrategyProfile",
        object_id=strategy_id, new_value={"regime": cfg.get("regime", {})},
        reason="regime gate updated via UI",
    )
    await db.commit()
    return redirect(
        f"/ui/strategies/{strategy_id}",
        flash="Filtro de régimen actualizado" if (enabled and allowed)
        else "Filtro de régimen desactivado",
    )


@router.post("/ui/strategies/{strategy_id}/sltp")
async def update_sltp(
    request: Request,
    strategy_id: str,
    db: AsyncSession = Depends(get_db),
    sl_atr_multiplier: str = Form(""),
    tp_atr_multiplier: str = Form(""),
) -> RedirectResponse:
    """Edit the SL/TP ATR multipliers. A TP enables the complete bracket (TP+SL)
    that some brokers require (else the entry fails with oto-orders-not-supported).
    Empty = None: SL falls back to the inherited default, TP off (no take profit).
    """
    prof_res = await db.execute(
        select(StrategyProfile).where(StrategyProfile.strategy_id == strategy_id)
    )
    profile = prof_res.scalar_one_or_none()
    if profile is None:
        profile = StrategyProfile(strategy_id=strategy_id)
        db.add(profile)

    def _pos(value: str) -> float | None:
        v = (value or "").strip()
        if not v:
            return None
        try:
            f = float(v)
            return f if f > 0 else None
        except ValueError:
            return None

    profile.sl_atr_multiplier = _pos(sl_atr_multiplier)
    profile.tp_atr_multiplier = _pos(tp_atr_multiplier)

    await AuditService().log(
        db, actor="admin", action="UPDATE", object_type="StrategyProfile",
        object_id=strategy_id,
        new_value={"sl_atr_multiplier": str(profile.sl_atr_multiplier),
                   "tp_atr_multiplier": str(profile.tp_atr_multiplier)},
        reason="SL/TP ATR multipliers updated via UI",
    )
    await db.commit()
    return redirect(
        f"/ui/strategies/{strategy_id}", flash="SL/TP por ATR actualizados",
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
