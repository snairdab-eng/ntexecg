"""Asset Profiles UI — list + detail + update."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.asset_profile import AssetProfile
from app.services.audit_service import AuditService
from app.web.common import render, redirect, flash_messages

router = APIRouter()


def _session_summary(cfg: dict | None) -> str:
    """Render session config as '09:30-15:45 ET Lun-Vie'."""
    if not cfg:
        return "—"
    start = cfg.get("entry_start", "?")
    end = cfg.get("entry_end", "?")
    days = cfg.get("days_enabled", [])
    day_label = "Dom-Vie" if 0 in days and 5 in days else "Lun-Vie"
    return f"{start}-{end} ET {day_label}"


@router.get("/ui/assets", response_class=HTMLResponse)
async def list_assets(
    request: Request, db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    result = await db.execute(select(AssetProfile).order_by(AssetProfile.symbol))
    assets = []
    for a in result.scalars().all():
        assets.append({
            "symbol": a.symbol, "name": a.name,
            "pine_script_config": a.pine_script_config,
            "session": _session_summary(a.session_config_json),
            "sl_atr_multiplier": float(a.sl_atr_multiplier) if a.sl_atr_multiplier else None,
            "score_minimum": a.score_minimum,
        })
    return await render(
        request, "assets.html",
        {"assets": assets, "messages": flash_messages(request)}, db=db,
    )


@router.get("/ui/assets/{symbol}", response_class=HTMLResponse)
async def asset_detail(
    request: Request, symbol: str, db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    result = await db.execute(select(AssetProfile).where(AssetProfile.symbol == symbol))
    asset = result.scalar_one_or_none()
    if asset is None:
        return redirect("/ui/assets", flash="Activo no encontrado", category="error")
    return await render(
        request, "asset_detail.html",
        {
            "asset": asset, "session": _session_summary(asset.session_config_json),
            "messages": flash_messages(request),
        }, db=db,
    )


@router.post("/ui/assets/{symbol}")
async def update_asset(
    request: Request,
    symbol: str,
    db: AsyncSession = Depends(get_db),
    sl_atr_multiplier: str = Form(""),
    score_minimum: str = Form(""),
    atr_period: str = Form(""),
) -> RedirectResponse:
    result = await db.execute(select(AssetProfile).where(AssetProfile.symbol == symbol))
    asset = result.scalar_one_or_none()
    if asset is None:
        return redirect("/ui/assets", flash="Activo no encontrado", category="error")

    old = {
        "sl_atr_multiplier": float(asset.sl_atr_multiplier) if asset.sl_atr_multiplier else None,
        "score_minimum": asset.score_minimum,
    }
    if sl_atr_multiplier:
        try:
            asset.sl_atr_multiplier = float(sl_atr_multiplier)
        except ValueError:
            pass
    if score_minimum:
        try:
            asset.score_minimum = int(score_minimum)
        except ValueError:
            pass
    if atr_period:
        try:
            asset.atr_period = int(atr_period)
        except ValueError:
            pass

    await AuditService().log(
        db, actor="admin", action="UPDATE", object_type="AssetProfile",
        object_id=symbol, old_value=old,
        new_value={"sl_atr_multiplier": sl_atr_multiplier, "score_minimum": score_minimum},
    )
    await db.commit()
    return redirect(f"/ui/assets/{symbol}", flash=f"Activo {symbol} actualizado")
