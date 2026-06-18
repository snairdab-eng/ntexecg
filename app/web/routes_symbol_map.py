"""Symbol Mapper UI — mappings table + create + toggle."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.symbol_map import SymbolMap
from app.services.audit_service import AuditService
from app.web.common import render, redirect, flash_messages

router = APIRouter()

_EXPIRY_WARN_DAYS = 7


@router.get("/ui/symbol-map", response_class=HTMLResponse)
async def list_symbol_map(
    request: Request, db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    result = await db.execute(select(SymbolMap).order_by(SymbolMap.tv_symbol))
    rows = list(result.scalars().all())
    warn_cutoff = date.today() + timedelta(days=_EXPIRY_WARN_DAYS)
    items = []
    for sm in rows:
        expiring = bool(sm.expiry_date and sm.expiry_date <= warn_cutoff)
        items.append({
            "id": sm.id, "tv_symbol": sm.tv_symbol, "mapped_symbol": sm.mapped_symbol,
            "market_data_symbol": sm.market_data_symbol,
            "exchange": sm.exchange, "contract_type": sm.contract_type,
            "pine_script_config": sm.pine_script_config, "expiry_date": sm.expiry_date,
            "active": sm.active, "expiring": expiring,
        })
    return await render(
        request, "symbol_map.html",
        {"mappings": items, "messages": flash_messages(request)}, db=db,
    )


@router.post("/ui/symbol-map/new")
async def create_mapping(
    request: Request,
    db: AsyncSession = Depends(get_db),
    tv_symbol: str = Form(...),
    mapped_symbol: str = Form(...),
    market_data_symbol: str = Form(""),
    exchange: str = Form("CME"),
    contract_type: str = Form("futures_micro"),
    expiry_date: str = Form(""),
) -> RedirectResponse:
    dup = await db.execute(select(SymbolMap).where(SymbolMap.tv_symbol == tv_symbol))
    if dup.scalar_one_or_none() is not None:
        return redirect(
            "/ui/symbol-map", flash=f"'{tv_symbol}' ya existe", category="error"
        )

    parsed_expiry = None
    if expiry_date:
        try:
            parsed_expiry = datetime.strptime(expiry_date, "%Y-%m-%d").date()
        except ValueError:
            pass

    # Empty market_data_symbol → None (symbol reads its own bridge data).
    sm = SymbolMap(
        tv_symbol=tv_symbol,
        mapped_symbol=mapped_symbol,
        market_data_symbol=market_data_symbol.strip() or None,
        exchange=exchange,
        contract_type=contract_type,
        pine_script_config=f'"ticker": "{tv_symbol}"',
        expiry_date=parsed_expiry,
        active=True,
    )
    db.add(sm)
    await AuditService().log(
        db, actor="admin", action="CREATE", object_type="SymbolMap",
        object_id=tv_symbol, new_value={"mapped_symbol": mapped_symbol},
    )
    await db.commit()
    return redirect("/ui/symbol-map", flash=f"Mapeo creado: {tv_symbol} → {mapped_symbol}")


@router.post("/ui/symbol-map/{mapping_id}/toggle")
async def toggle_mapping(
    request: Request, mapping_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> RedirectResponse:
    result = await db.execute(select(SymbolMap).where(SymbolMap.id == mapping_id))
    sm = result.scalar_one_or_none()
    if sm is None:
        return redirect("/ui/symbol-map", flash="Mapeo no encontrado", category="error")
    sm.active = not sm.active
    await AuditService().log(
        db, actor="admin", action="UPDATE", object_type="SymbolMap",
        object_id=sm.tv_symbol, new_value={"active": sm.active},
    )
    await db.commit()
    state = "activado" if sm.active else "desactivado"
    return redirect("/ui/symbol-map", flash=f"{sm.tv_symbol} {state}")
