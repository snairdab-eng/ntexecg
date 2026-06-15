"""Positions UI — estimated state + flatten/lock/unlock actions."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.position_state import PositionState
from app.services.position_service import PositionService
from app.web.common import render, redirect, flash_messages

router = APIRouter()


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
    pos = await _position_target(db, position_id)
    if pos is None:
        return redirect("/ui/positions", flash="Posición no encontrada", category="error")
    await PositionService().on_flatten_manual(
        db, pos.strategy_id or "", pos.account_id, pos.symbol, actor="admin"
    )
    await db.commit()
    return redirect("/ui/positions", flash=f"Flatten enviado: {pos.symbol}")


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
