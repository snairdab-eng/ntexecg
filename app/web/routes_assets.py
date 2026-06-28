"""Asset Profiles UI — Calibración por Activo (esquema real confirmado).

asset_profiles: contract_type (no 'instrument'), active (no 'enabled').
production/shadow NO existe aquí → vive en Strategy.status.
Vínculo activo↔estrategia por string: Strategy.asset_symbol == AssetProfile.symbol.
Campos escalonados (scale_entry_*) NO existen → solo diseño pendiente en el template.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.asset_profile import AssetProfile
from app.models.strategy import Strategy
from app.services.audit_service import AuditService
from app.web.common import render, redirect, flash_messages

router = APIRouter()

VALID_TF = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"}
LIVE_STATUSES = {"paper", "micro", "limited_live", "live"}
_DAY_NAMES = ["Dom", "Lun", "Mar", "Mié", "Jue", "Vie", "Sáb"]


def readable_window(cfg: dict | None) -> dict:
    """Ventana legible. Si el config trae 'windows' (repetibles, override de estrategia),
    se resume la primera (SessionValidator usa 'windows' cuando existe)."""
    cfg = cfg or {}
    wins = cfg.get("windows")
    if wins:
        w = wins[0]
        days = w.get("days", w.get("days_enabled", [])) or []
        nde = bool(w.get("next_day_end", False))
        return {
            "start": w.get("start", w.get("entry_start", "")),
            "end": w.get("end", w.get("entry_end", "")),
            "days": days,
            "days_label": ", ".join(_DAY_NAMES[d] for d in days if 0 <= d <= 6) or "—",
            "nde": nde, "overnight": nde,
            "timezone": cfg.get("timezone", "America/New_York"),
            "n_windows": len(wins),
        }
    days = cfg.get("days_enabled", []) or []
    nde = bool(cfg.get("next_day_end", False))
    return {
        "start": cfg.get("entry_start", ""),
        "end": cfg.get("entry_end", ""),
        "days": days,
        "days_label": ", ".join(_DAY_NAMES[d] for d in days if 0 <= d <= 6) or "—",
        "nde": nde,
        "overnight": nde or bool(cfg.get("allow_overnight", False)),
        "timezone": cfg.get("timezone", "America/New_York"),
        "n_windows": 0,
    }


def _session_summary(cfg: dict | None) -> str:
    w = readable_window(cfg)
    if not w["start"]:
        return "—"
    tag = " overnight" if w["overnight"] else ""
    return f"{w['start']}-{w['end']} ET [{w['days_label']}]{tag}"


async def _strategies_by_symbol(db: AsyncSession) -> dict[str, list[Strategy]]:
    rows = (await db.execute(select(Strategy))).scalars().all()
    out: dict[str, list[Strategy]] = {}
    for s in rows:
        if s.asset_symbol:
            out.setdefault(s.asset_symbol, []).append(s)
    return out


def _strat_view(s: Strategy) -> dict:
    return {"id": str(s.id), "strategy_id": s.strategy_id, "name": s.name,
            "asset_symbol": s.asset_symbol, "status": s.status, "enabled": s.enabled}


def _asset_view(a: AssetProfile, strats: list[Strategy]) -> dict:
    return {
        "id": str(a.id),
        "symbol": a.symbol,
        "name": a.name,
        "contract_type": a.contract_type,
        "active": bool(a.active),
        "pine_script_config": a.pine_script_config,
        "session": _session_summary(a.session_config_json),
        "window": readable_window(a.session_config_json),
        "sl_atr_multiplier": float(a.sl_atr_multiplier) if a.sl_atr_multiplier is not None else None,
        "tp_atr_multiplier": float(a.tp_atr_multiplier) if a.tp_atr_multiplier is not None else None,
        "atr_timeframe": a.atr_timeframe,
        "score_minimum": a.score_minimum,
        "atr_period": a.atr_period,
        "strategies": [_strat_view(s) for s in strats],
        "strategy_statuses": sorted({s.status for s in strats}),
        "multi_strategy": len(strats) > 1,
        "has_live": any(s.status in LIVE_STATUSES for s in strats),
    }


@router.get("/ui/assets", response_class=HTMLResponse)
async def list_assets(request: Request, db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    result = await db.execute(select(AssetProfile).order_by(AssetProfile.symbol))
    by_sym = await _strategies_by_symbol(db)
    assets = [_asset_view(a, by_sym.get(a.symbol, [])) for a in result.scalars().all()]
    return await render(request, "assets.html",
                        {"assets": assets, "messages": flash_messages(request)}, db=db)


@router.get("/ui/assets/{symbol}", response_class=HTMLResponse)
async def asset_detail(request: Request, symbol: str, db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    a = (await db.execute(select(AssetProfile).where(AssetProfile.symbol == symbol))).scalar_one_or_none()
    if a is None:
        return redirect("/ui/assets", flash="Activo no encontrado", category="error")
    by_sym = await _strategies_by_symbol(db)
    view = _asset_view(a, by_sym.get(symbol, []))
    return await render(request, "asset_detail.html",
                        {"asset": view, "valid_tf": sorted(VALID_TF), "day_names": _DAY_NAMES,
                         "messages": flash_messages(request)}, db=db)


def _validate_scalars(sl: str, atr_tf: str) -> str | None:
    if sl:
        try:
            if float(sl) <= 0:
                return "sl_atr_multiplier debe ser > 0"
        except ValueError:
            return "sl_atr_multiplier inválido"
    if atr_tf and atr_tf not in VALID_TF:
        return "atr_timeframe inválido (válidos: " + ", ".join(sorted(VALID_TF)) + ")"
    return None


def _validate_window(start: str, end: str, days: list[int]) -> str | None:
    for label, t in (("inicio", start), ("fin", end)):
        if t:
            parts = t.split(":")
            if len(parts) != 2 or not (parts[0].isdigit() and parts[1].isdigit()):
                return f"horario de {label} debe ser HH:MM"
            h, m = int(parts[0]), int(parts[1])
            if not (0 <= h <= 23 and 0 <= m <= 59):
                return f"horario de {label} fuera de rango"
    if not days:
        return "days no puede estar vacío"
    return None


@router.post("/ui/assets/{symbol}")
async def update_asset(
    request: Request,
    symbol: str,
    db: AsyncSession = Depends(get_db),
    active: str = Form(""),
    entry_start: str = Form(""),
    entry_end: str = Form(""),
    days: list[str] = Form([]),
    next_day_end: str = Form(""),
    sl_atr_multiplier: str = Form(""),
    atr_timeframe: str = Form(""),
    tp_atr_multiplier: str = Form(""),
    score_minimum: str = Form(""),
    atr_period: str = Form(""),
    confirm: str = Form(""),
    form_full: str = Form(""),
    form_active: str = Form(""),
) -> RedirectResponse:
    a = (await db.execute(select(AssetProfile).where(AssetProfile.symbol == symbol))).scalar_one_or_none()
    if a is None:
        return redirect("/ui/assets", flash="Activo no encontrado", category="error")

    full = form_full == "1"
    days_int = [int(d) for d in days if str(d).strip().lstrip("-").isdigit()]
    err = _validate_scalars(sl_atr_multiplier, atr_timeframe)
    if full:
        err = err or _validate_window(entry_start, entry_end, days_int)
    if err:
        return redirect(f"/ui/assets/{symbol}", flash=err, category="error")

    by_sym = await _strategies_by_symbol(db)
    strats = by_sym.get(symbol, [])
    if any(s.status in LIVE_STATUSES for s in strats) and confirm != "yes":
        return redirect(f"/ui/assets/{symbol}",
                        flash="Este activo tiene estrategias en paper/micro/live: confirma el cambio.",
                        category="error")

    old = {
        "active": bool(a.active),
        "sl_atr_multiplier": float(a.sl_atr_multiplier) if a.sl_atr_multiplier is not None else None,
        "atr_timeframe": a.atr_timeframe,
        "tp_atr_multiplier": float(a.tp_atr_multiplier) if a.tp_atr_multiplier is not None else None,
    }

    if full:
        a.active = (active in ("on", "yes", "true"))
        cfg = dict(a.session_config_json or {})
        cfg.setdefault("timezone", "America/New_York")
        cfg.setdefault("allow_exits_outside_window", True)
        if entry_start:
            cfg["entry_start"] = entry_start
        if entry_end:
            cfg["entry_end"] = entry_end
        cfg["days_enabled"] = days_int
        cfg["next_day_end"] = (next_day_end in ("on", "yes"))
        cfg["allow_overnight"] = cfg["next_day_end"]
        a.session_config_json = cfg

    if form_active == "1":
        a.active = (active in ("on", "yes", "true"))

    if sl_atr_multiplier:
        a.sl_atr_multiplier = float(sl_atr_multiplier)
    if atr_timeframe:
        a.atr_timeframe = atr_timeframe
    if tp_atr_multiplier:
        try:
            a.tp_atr_multiplier = float(tp_atr_multiplier)
        except ValueError:
            pass
    if score_minimum:
        try:
            a.score_minimum = int(score_minimum)
        except ValueError:
            pass
    if atr_period:
        try:
            a.atr_period = int(atr_period)
        except ValueError:
            pass
    a.version = (a.version or 1) + 1
    a.updated_by = "ui"

    await AuditService().log(
        db, actor="admin", action="UPDATE", object_type="AssetProfile",
        object_id=symbol, old_value=old,
        new_value={"active": a.active, "sl_atr_multiplier": sl_atr_multiplier,
                   "atr_timeframe": atr_timeframe, "tp_atr_multiplier": tp_atr_multiplier},
    )
    await db.commit()
    warn = "  varias estrategias usan este simbolo" if len(strats) > 1 else ""
    return redirect(f"/ui/assets/{symbol}", flash=f"Activo {symbol} actualizado{warn}")
