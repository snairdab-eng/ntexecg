"""JSON API — Asset Profiles + Strategy (config efectiva, calibración, scale-entry, status).

Endpoints:
  GET    /api/asset-profiles                       · GET /api/asset-profiles/{id} · PATCH …
  GET    /api/strategies?asset_symbol=...
  PATCH  /api/strategies/{id}/status
  GET    /api/strategies/{id}/config               → inherited (asset) / override (strategy) / effective (resolver) / scale_entry
  PATCH  /api/strategies/{id}/calibration          → sl_atr_multiplier, atr_timeframe, tp_atr_multiplier, windows (StrategyProfile)
  PATCH  /api/strategies/{id}/scale-entry          → diseño escalonado (pipeline_config_json["scale_entry"]) — NO ejecución

La calibración vive en StrategyProfile (ConfigResolver lo prioriza sobre asset_profiles).
NO se implementa ejecución escalonada: scale_entry es solo diseño; mode=enabled se rechaza.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.asset_profile import AssetProfile
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from app.services.audit_service import AuditService
from app.services.config_resolver import ConfigResolver
from app.web.routes_assets import (
    VALID_TF, LIVE_STATUSES, readable_window, _asset_view, _strategies_by_symbol,
)

router = APIRouter(prefix="/api", tags=["api"])

_VALID_STATUSES = {
    "candidate", "shadow", "paper", "micro", "limited_live", "live",
    "paused", "quarantined", "retired",
}
SCALE_STOP_MODES = {"common_position_stop"}
SCALE_MODES = {"design_only", "off"}  # "enabled" NO permitido (motor no existe)


# ───────────────────────── asset profiles (sin cambios) ─────────────────────
class WindowPatch(BaseModel):
    entry_start: str | None = None
    entry_end: str | None = None
    days_enabled: list[int] | None = None
    next_day_end: bool | None = None


class AssetProfilePatch(BaseModel):
    active: bool | None = None
    sl_atr_multiplier: float | None = Field(default=None, gt=0)
    atr_timeframe: str | None = None
    tp_atr_multiplier: float | None = Field(default=None, gt=0)
    session: WindowPatch | None = None
    confirm: bool = False


class StatusPatch(BaseModel):
    status: str


def _hhmm(t: str, label: str) -> None:
    parts = t.split(":")
    if len(parts) != 2 or not (parts[0].isdigit() and parts[1].isdigit()):
        raise HTTPException(422, f"horario de {label} debe ser HH:MM")
    if not (0 <= int(parts[0]) <= 23 and 0 <= int(parts[1]) <= 59):
        raise HTTPException(422, f"horario de {label} fuera de rango")


@router.get("/asset-profiles")
async def list_asset_profiles(db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (await db.execute(select(AssetProfile).order_by(AssetProfile.symbol))).scalars().all()
    by_sym = await _strategies_by_symbol(db)
    return [_asset_view(a, by_sym.get(a.symbol, [])) for a in rows]


async def _asset_by_id(db: AsyncSession, id: str) -> AssetProfile:
    try:
        uid = uuid.UUID(id)
    except ValueError:
        raise HTTPException(400, "id inválido")
    a = (await db.execute(select(AssetProfile).where(AssetProfile.id == uid))).scalar_one_or_none()
    if a is None:
        raise HTTPException(404, "asset profile no encontrado")
    return a


@router.get("/asset-profiles/{id}")
async def get_asset_profile(id: str, db: AsyncSession = Depends(get_db)) -> dict:
    a = await _asset_by_id(db, id)
    by_sym = await _strategies_by_symbol(db)
    return _asset_view(a, by_sym.get(a.symbol, []))


@router.patch("/asset-profiles/{id}")
async def patch_asset_profile(id: str, body: AssetProfilePatch, db: AsyncSession = Depends(get_db)) -> dict:
    a = await _asset_by_id(db, id)
    if body.atr_timeframe is not None and body.atr_timeframe not in VALID_TF:
        raise HTTPException(422, f"atr_timeframe inválido (válidos: {', '.join(sorted(VALID_TF))})")
    by_sym = await _strategies_by_symbol(db)
    strats = by_sym.get(a.symbol, [])
    if any(s.status in LIVE_STATUSES for s in strats) and not body.confirm:
        raise HTTPException(409, "El activo tiene estrategias en paper/micro/live. Reenvía con confirm=true.")
    old = _asset_view(a, strats)
    if body.active is not None:
        a.active = body.active
    if body.sl_atr_multiplier is not None:
        a.sl_atr_multiplier = body.sl_atr_multiplier
    if body.atr_timeframe is not None:
        a.atr_timeframe = body.atr_timeframe
    if body.tp_atr_multiplier is not None:
        a.tp_atr_multiplier = body.tp_atr_multiplier
    if body.session is not None:
        cfg = dict(a.session_config_json or {})
        cfg.setdefault("timezone", "America/New_York")
        cfg.setdefault("allow_exits_outside_window", True)
        s = body.session
        if s.entry_start is not None:
            _hhmm(s.entry_start, "inicio"); cfg["entry_start"] = s.entry_start
        if s.entry_end is not None:
            _hhmm(s.entry_end, "fin"); cfg["entry_end"] = s.entry_end
        if s.days_enabled is not None:
            if not s.days_enabled:
                raise HTTPException(422, "days_enabled no puede estar vacío")
            if any(d < 0 or d > 6 for d in s.days_enabled):
                raise HTTPException(422, "days_enabled: valores 0..6 (0=Dom)")
            cfg["days_enabled"] = s.days_enabled
        if s.next_day_end is not None:
            cfg["next_day_end"] = s.next_day_end
            cfg["allow_overnight"] = s.next_day_end
        a.session_config_json = cfg
    a.version = (a.version or 1) + 1
    a.updated_by = "api"
    await AuditService().log(db, actor="api", action="UPDATE", object_type="AssetProfile",
                             object_id=a.symbol, old_value={"sl": old["sl_atr_multiplier"]},
                             new_value=body.model_dump(exclude_none=True))
    await db.commit()
    return _asset_view(a, strats)


# ───────────────────────── strategies ───────────────────────────────────────
@router.get("/strategies")
async def list_strategies_api(asset_symbol: str | None = Query(default=None),
                              db: AsyncSession = Depends(get_db)) -> list[dict]:
    stmt = select(Strategy)
    if asset_symbol:
        stmt = stmt.where(Strategy.asset_symbol == asset_symbol)
    rows = (await db.execute(stmt.order_by(Strategy.asset_symbol, Strategy.created_at))).scalars().all()
    return [{"id": str(s.id), "strategy_id": s.strategy_id, "name": s.name,
             "asset_symbol": s.asset_symbol, "status": s.status, "enabled": s.enabled}
            for s in rows]


async def _strategy(db: AsyncSession, sid: str) -> Strategy:
    s = (await db.execute(select(Strategy).where(Strategy.strategy_id == sid))).scalar_one_or_none()
    if s is None:
        raise HTTPException(404, "estrategia no encontrada")
    return s


async def _profile(db: AsyncSession, sid: str, create: bool = False) -> StrategyProfile | None:
    p = (await db.execute(select(StrategyProfile).where(StrategyProfile.strategy_id == sid))).scalar_one_or_none()
    if p is None and create:
        p = StrategyProfile(strategy_id=sid)
        db.add(p)
    return p


def _fl(v):
    return float(v) if v is not None else None


@router.patch("/strategies/{id}/status")
async def patch_strategy_status(id: str, body: StatusPatch, db: AsyncSession = Depends(get_db)) -> dict:
    if body.status not in _VALID_STATUSES:
        raise HTTPException(422, f"status inválido (válidos: {', '.join(sorted(_VALID_STATUSES))})")
    try:
        uid = uuid.UUID(id)
    except ValueError:
        # permitir también por strategy_id
        s = (await db.execute(select(Strategy).where(Strategy.strategy_id == id))).scalar_one_or_none()
    else:
        s = (await db.execute(select(Strategy).where(Strategy.id == uid))).scalar_one_or_none()
    if s is None:
        raise HTTPException(404, "estrategia no encontrada")
    old = s.status
    s.status = body.status
    await AuditService().log(db, actor="api", action="STATUS_CHANGE", object_type="Strategy",
                             object_id=s.strategy_id, old_value={"status": old},
                             new_value={"status": body.status})
    await db.commit()
    return {"id": str(s.id), "strategy_id": s.strategy_id, "status": s.status}


@router.get("/strategies/{strategy_id}/config")
async def get_strategy_config(strategy_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    s = await _strategy(db, strategy_id)
    p = await _profile(db, strategy_id)
    asset = None
    if s.asset_symbol:
        asset = (await db.execute(
            select(AssetProfile).where(AssetProfile.symbol == s.asset_symbol))).scalar_one_or_none()
    eff = await ConfigResolver().resolve(db, strategy_id, s.asset_symbol)
    pcfg = (p.pipeline_config_json or {}) if p else {}
    return {
        "strategy_id": s.strategy_id, "asset_symbol": s.asset_symbol, "status": s.status,
        "inherited": {
            "sl_atr_multiplier": _fl(asset.sl_atr_multiplier) if asset else None,
            "atr_timeframe": asset.atr_timeframe if asset else None,
            "tp_atr_multiplier": _fl(asset.tp_atr_multiplier) if asset else None,
            "window": readable_window(asset.session_config_json) if asset else None,
        },
        "override": {
            "sl_atr_multiplier": _fl(p.sl_atr_multiplier) if p else None,
            "atr_timeframe": p.atr_timeframe if p else None,
            "tp_atr_multiplier": _fl(p.tp_atr_multiplier) if p else None,
            "windows": pcfg.get("windows"),
        },
        "effective": {
            "sl_atr_multiplier": eff.get("sl_atr_multiplier"),
            "atr_timeframe": eff.get("atr_timeframe"),
            "tp_atr_multiplier": eff.get("tp_atr_multiplier"),
            "window": readable_window(eff.get("session_config_json")),
        },
        "scale_entry": pcfg.get("scale_entry"),
    }


class WindowItem(BaseModel):
    days: list[int]
    start: str
    end: str
    next_day_end: bool = False


class CalibrationPatch(BaseModel):
    sl_atr_multiplier: float | None = Field(default=None, gt=0)
    atr_timeframe: str | None = None
    tp_atr_multiplier: float | None = Field(default=None, gt=0)
    windows: list[WindowItem] | None = None


@router.patch("/strategies/{strategy_id}/calibration")
async def patch_calibration(strategy_id: str, body: CalibrationPatch,
                            db: AsyncSession = Depends(get_db)) -> dict:
    s = await _strategy(db, strategy_id)
    if body.atr_timeframe is not None and body.atr_timeframe not in VALID_TF:
        raise HTTPException(422, f"atr_timeframe inválido (válidos: {', '.join(sorted(VALID_TF))})")
    p = await _profile(db, strategy_id, create=True)
    old = {"sl": _fl(p.sl_atr_multiplier), "atr_tf": p.atr_timeframe,
           "tp": _fl(p.tp_atr_multiplier), "windows": (p.pipeline_config_json or {}).get("windows")}
    if body.sl_atr_multiplier is not None:
        p.sl_atr_multiplier = body.sl_atr_multiplier
    if body.atr_timeframe is not None:
        p.atr_timeframe = body.atr_timeframe
    if body.tp_atr_multiplier is not None:
        p.tp_atr_multiplier = body.tp_atr_multiplier
    if body.windows is not None:
        for w in body.windows:
            if not w.days or any(d < 0 or d > 6 for d in w.days):
                raise HTTPException(422, "windows.days inválido (0..6, no vacío)")
            _hhmm(w.start, "inicio"); _hhmm(w.end, "fin")
        cfg = dict(p.pipeline_config_json or {})
        cfg["windows"] = [w.model_dump() for w in body.windows]
        p.pipeline_config_json = cfg
    p.updated_by = "api"
    await AuditService().log(db, actor="api", action="UPDATE", object_type="StrategyProfile",
                             object_id=strategy_id, old_value=old,
                             new_value=body.model_dump(exclude_none=True))
    await db.commit()
    return await get_strategy_config(strategy_id, db)


class ScaleEntryPatch(BaseModel):
    mode: str = "design_only"
    levels: list[float] | None = None
    quantities: list[int] | None = None
    max_micro_contracts: int | None = Field(default=None, ge=1, le=50)
    stop_mode: str = "common_position_stop"


@router.patch("/strategies/{strategy_id}/scale-entry")
async def patch_scale_entry(strategy_id: str, body: ScaleEntryPatch,
                            db: AsyncSession = Depends(get_db)) -> dict:
    # Rechazo explícito: el motor escalonado NO existe (solo diseño).
    if body.mode == "enabled":
        raise HTTPException(
            422, "scale_entry_mode=enabled rechazado: el motor de ejecución escalonada aún no "
                 "existe. Solo se permite 'design_only' (diseño). NTEXECG opera 1 entrada + bracket.")
    if body.mode not in SCALE_MODES:
        raise HTTPException(422, f"mode inválido (válidos: {', '.join(sorted(SCALE_MODES))})")
    if body.stop_mode not in SCALE_STOP_MODES:
        raise HTTPException(422, f"stop_mode inválido (válidos: {', '.join(sorted(SCALE_STOP_MODES))})")
    s = await _strategy(db, strategy_id)
    p = await _profile(db, strategy_id, create=True)
    cfg = dict(p.pipeline_config_json or {})
    before = cfg.get("scale_entry")
    if body.mode == "off":
        cfg.pop("scale_entry", None)
        se = None
    else:
        if body.levels is not None and any(x <= 0 for x in body.levels):
            raise HTTPException(422, "levels deben ser > 0 (múltiplos de ATR)")
        if body.quantities is not None and any(q < 0 for q in body.quantities):
            raise HTTPException(422, "quantities no pueden ser negativas")
        se = {
            "mode": "design_only",  # forzado: nunca enabled
            "levels": body.levels or [],
            "quantities": body.quantities or [],
            "max_micro_contracts": body.max_micro_contracts,
            "stop_mode": body.stop_mode,
        }
        cfg["scale_entry"] = se
    p.pipeline_config_json = cfg or None
    p.updated_by = "api"
    await AuditService().log(db, actor="api", action="UPDATE", object_type="StrategyProfile",
                             object_id=strategy_id, old_value={"scale_entry": before},
                             new_value={"scale_entry": se}, reason="scale_entry design (no execution)")
    await db.commit()
    return {"strategy_id": s.strategy_id, "scale_entry": se,
            "note": "Diseño solamente — sin ejecución escalonada (1 entrada + bracket)."}
