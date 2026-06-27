"""JSON API — Asset Profiles + Strategy status (esquema real confirmado).

Endpoints:
  GET    /api/asset-profiles
  GET    /api/asset-profiles/{id}
  PATCH  /api/asset-profiles/{id}
  GET    /api/strategies?asset_symbol=...
  PATCH  /api/strategies/{id}/status

Solo toca lo soportado: active, session_config_json (ventana), sl_atr_multiplier,
atr_timeframe, tp_atr_multiplier en asset_profiles; y Strategy.status en strategies.
NO existe production/shadow en asset_profiles (eso vive en Strategy.status).
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
from app.services.audit_service import AuditService
from app.web.routes_assets import (
    VALID_TF, LIVE_STATUSES, readable_window, _asset_view, _strategies_by_symbol,
)

router = APIRouter(prefix="/api", tags=["api"])

_VALID_STATUSES = {
    "candidate", "shadow", "paper", "micro", "limited_live", "live",
    "paused", "quarantined", "retired",
}


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
    confirm: bool = False  # requerido si hay estrategias live-ish


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


async def _get_by_id(db: AsyncSession, id: str) -> AssetProfile:
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
    a = await _get_by_id(db, id)
    by_sym = await _strategies_by_symbol(db)
    return _asset_view(a, by_sym.get(a.symbol, []))


@router.patch("/asset-profiles/{id}")
async def patch_asset_profile(
    id: str, body: AssetProfilePatch, db: AsyncSession = Depends(get_db)
) -> dict:
    a = await _get_by_id(db, id)

    if body.atr_timeframe is not None and body.atr_timeframe not in VALID_TF:
        raise HTTPException(422, f"atr_timeframe inválido (válidos: {', '.join(sorted(VALID_TF))})")

    by_sym = await _strategies_by_symbol(db)
    strats = by_sym.get(a.symbol, [])
    if any(s.status in LIVE_STATUSES for s in strats) and not body.confirm:
        raise HTTPException(
            409, "El activo tiene estrategias en paper/micro/live. Reenvía con confirm=true.")

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
    await AuditService().log(
        db, actor="api", action="UPDATE", object_type="AssetProfile",
        object_id=a.symbol, old_value={"active": old["active"], "sl": old["sl_atr_multiplier"],
                                       "atr_tf": old["atr_timeframe"]},
        new_value=body.model_dump(exclude_none=True),
    )
    await db.commit()
    return _asset_view(a, strats)


@router.get("/strategies")
async def list_strategies_api(
    asset_symbol: str | None = Query(default=None), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    stmt = select(Strategy)
    if asset_symbol:
        stmt = stmt.where(Strategy.asset_symbol == asset_symbol)
    rows = (await db.execute(stmt.order_by(Strategy.asset_symbol, Strategy.created_at))).scalars().all()
    return [{"id": str(s.id), "strategy_id": s.strategy_id, "name": s.name,
             "asset_symbol": s.asset_symbol, "status": s.status, "enabled": s.enabled}
            for s in rows]


@router.patch("/strategies/{id}/status")
async def patch_strategy_status(
    id: str, body: StatusPatch, db: AsyncSession = Depends(get_db)
) -> dict:
    if body.status not in _VALID_STATUSES:
        raise HTTPException(422, f"status inválido (válidos: {', '.join(sorted(_VALID_STATUSES))})")
    try:
        uid = uuid.UUID(id)
    except ValueError:
        raise HTTPException(400, "id inválido")
    s = (await db.execute(select(Strategy).where(Strategy.id == uid))).scalar_one_or_none()
    if s is None:
        raise HTTPException(404, "estrategia no encontrada")
    old = s.status
    s.status = body.status
    await AuditService().log(
        db, actor="api", action="STATUS_CHANGE", object_type="Strategy",
        object_id=s.strategy_id, old_value={"status": old}, new_value={"status": body.status},
    )
    await db.commit()
    return {"id": str(s.id), "strategy_id": s.strategy_id, "status": s.status}
