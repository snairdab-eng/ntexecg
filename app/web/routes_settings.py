"""Settings UI — global config + bridge config."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings as app_settings
from app.db.session import get_db
from app.models.global_profile import GlobalProfile
from app.services.audit_service import AuditService
from app.web.common import render, redirect, flash_messages

router = APIRouter()


async def _get_or_create_global(db: AsyncSession) -> GlobalProfile:
    result = await db.execute(
        select(GlobalProfile).where(GlobalProfile.active.is_(True)).limit(1)
    )
    gp = result.scalar_one_or_none()
    if gp is None:
        gp = GlobalProfile(profile_name="default")
        db.add(gp)
        await db.flush()
    return gp


@router.get("/ui/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request, db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    gp = await _get_or_create_global(db)
    await db.commit()
    return await render(
        request, "settings.html",
        {
            "gp": gp,
            "market_provider": app_settings.MARKET_DATA_PROVIDER,
            "bridge_path": app_settings.NTBRIDGE_PATH,
            "heartbeat_max_age": app_settings.NTBRIDGE_HEARTBEAT_MAX_AGE,
            "tp_env_enabled": app_settings.TRADERSPOST_ENABLED,
            "messages": flash_messages(request),
        }, db=db,
    )


@router.post("/ui/settings")
async def update_settings(
    request: Request,
    db: AsyncSession = Depends(get_db),
    mode: str = Form("normal"),
    max_open_positions: str = Form(""),
    daily_loss_stop: str = Form(""),
    score_minimum: str = Form(""),
    news_window_minutes: str = Form(""),
    retry_attempts: str = Form(""),
) -> RedirectResponse:
    gp = await _get_or_create_global(db)
    old = {
        "mode": gp.mode, "max_open_positions": gp.max_open_positions,
        "score_minimum": gp.score_minimum,
    }

    if mode in ("normal", "defensive", "flatten_only", "paused"):
        gp.mode = mode
    for field, raw, caster in [
        ("max_open_positions", max_open_positions, int),
        ("score_minimum", score_minimum, int),
        ("news_window_minutes", news_window_minutes, int),
        ("retry_attempts", retry_attempts, int),
    ]:
        if raw:
            try:
                setattr(gp, field, caster(raw))
            except ValueError:
                pass
    if daily_loss_stop:
        try:
            gp.daily_loss_stop = float(daily_loss_stop)
        except ValueError:
            pass

    await AuditService().log(
        db, actor="admin", action="GLOBAL_MODE_CHANGE", object_type="GlobalProfile",
        object_id="default", old_value=old,
        new_value={"mode": gp.mode, "max_open_positions": gp.max_open_positions},
        reason="updated via settings UI",
    )
    await db.commit()
    return redirect("/ui/settings", flash="Configuración global actualizada")


@router.post("/ui/settings/dispatch")
async def update_global_dispatch(
    request: Request,
    db: AsyncSession = Depends(get_db),
    action: str = Form(...),
    confirm: str = Form(""),
) -> RedirectResponse:
    """Fase 2 — arm/disarm real dispatch at the GLOBAL level (CONFIRMAR to arm)."""
    gp = await _get_or_create_global(db)
    if action == "arm":
        if confirm != "CONFIRMAR":
            return redirect("/ui/settings",
                            flash="Escribe CONFIRMAR para armar el despacho global",
                            category="error")
        gp.traderspost_enabled = True
        gp.dry_run = False
        msg = "Despacho global ARMADO (cada estrategia y el kill-switch del servidor siguen mandando)"
    elif action == "disarm":
        gp.dry_run = True
        msg = "Despacho global de vuelta en DRY_RUN"
    else:
        return redirect("/ui/settings", flash="Acción inválida", category="error")

    await AuditService().log(
        db, actor="admin", action="DISPATCH_CHANGE", object_type="GlobalProfile",
        object_id="default",
        new_value={"action": action, "traderspost_enabled": gp.traderspost_enabled,
                   "dry_run": gp.dry_run},
        reason="global dispatch toggled via UI")
    await db.commit()
    return redirect("/ui/settings", flash=msg)
