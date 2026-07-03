"""Positions UI — estimated state + flatten/lock/unlock actions."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings as app_settings
from app.db.session import get_db
from app.models.position_state import PositionState
from app.services.position_service import PositionService
from app.web.common import render, redirect, flash_messages

router = APIRouter()

# Estados con algo que cerrar del lado del broker (real o pendiente).
_FLATTENABLE = {"LONG", "SHORT", "PENDING_LONG", "PENDING_SHORT",
                "EXITING", "LOCKED", "UNKNOWN"}


@router.get("/ui/positions", response_class=HTMLResponse)
async def list_positions(
    request: Request, db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    result = await db.execute(
        select(PositionState).order_by(PositionState.symbol, PositionState.account_id)
    )
    positions = list(result.scalars().all())
    return await render(
        request, "positions.html",
        {"positions": positions, "messages": flash_messages(request)}, db=db,
    )


async def _position_target(
    db: AsyncSession, position_id: uuid.UUID
) -> PositionState | None:
    result = await db.execute(
        select(PositionState).where(PositionState.id == position_id)
    )
    return result.scalar_one_or_none()


@router.post("/ui/positions/{position_id}/flatten")
async def flatten(
    request: Request, position_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> RedirectResponse:
    """NX-06 — Flatten despacha un cierre REAL (vía dispatch_forced_exit, mismo
    gate Fase-2 que cualquier salida) y reporta el status verdadero. Antes solo
    marcaba EXITING en el estimado y decía "Flatten enviado" sin enviar nada.
    Solo cierra — nunca abre; el kill-switch por capas sigue mandando."""
    pos = await _position_target(db, position_id)
    if pos is None:
        return redirect("/ui/positions", flash="Posición no encontrada", category="error")
    if pos.state not in _FLATTENABLE:
        return redirect(
            "/ui/positions",
            flash=f"{pos.symbol} ya está {pos.state} — nada que cerrar",
            category="warning",
        )

    from app.services.config_resolver import ConfigResolver
    from app.services.forced_exit import dispatch_forced_exit
    from app.services.repositories import get_strategy_by_id

    strategy = await get_strategy_by_id(db, pos.strategy_id or "")
    if strategy is None:
        return redirect(
            "/ui/positions",
            flash=f"{pos.symbol}: sin estrategia asociada — no se puede "
                  "resolver el webhook de cierre",
            category="error",
        )
    config = await ConfigResolver().resolve(
        db, strategy.strategy_id, strategy.asset_symbol
    )
    result = await dispatch_forced_exit(
        db, pos, strategy, config, "manual_flatten", app_settings, actor="admin"
    )
    await db.commit()
    cat = "success" if result.status in ("SENT", "DRY_RUN") else "error"
    return redirect(
        "/ui/positions",
        flash=f"Flatten {result.status}: {pos.symbol}", category=cat,
    )


@router.post("/ui/positions/{position_id}/lock")
async def lock(
    request: Request, position_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> RedirectResponse:
    pos = await _position_target(db, position_id)
    if pos is None:
        return redirect("/ui/positions", flash="Posición no encontrada", category="error")
    await PositionService().on_lock(
        db, pos.strategy_id or "", pos.account_id, pos.symbol, actor="admin"
    )
    await db.commit()
    return redirect("/ui/positions", flash=f"Bloqueada: {pos.symbol}")


@router.post("/ui/positions/{position_id}/unlock")
async def unlock(
    request: Request, position_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> RedirectResponse:
    pos = await _position_target(db, position_id)
    if pos is None:
        return redirect("/ui/positions", flash="Posición no encontrada", category="error")
    await PositionService().on_unlock(
        db, pos.strategy_id or "", pos.account_id, pos.symbol, actor="admin"
    )
    await db.commit()
    return redirect("/ui/positions", flash=f"Desbloqueada: {pos.symbol}")
