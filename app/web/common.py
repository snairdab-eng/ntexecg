"""Shared web layer helpers: single Jinja2 instance + base context.

Every page gets app metadata, dry_run flag, and the global system mode so the
navbar badges (doc 02 §3) render consistently without each route repeating it.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

_templates_dir = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))


async def base_context(request: Request, db: AsyncSession | None = None) -> dict:
    """Return the context shared by every full-page render.

    global_mode comes from the active GlobalProfile when a db session is given,
    otherwise falls back to NORMAL (Phase 1 default).
    """
    global_mode = "normal"
    if db is not None:
        try:
            from app.services.repositories import get_global_profile
            gp = await get_global_profile(db)
            if gp is not None:
                global_mode = gp.mode
        except Exception:
            pass

    return {
        "app_name": settings.APP_NAME,
        "app_version": settings.APP_VERSION,
        "app_env": settings.APP_ENV,
        # "DRY RUN" badge = real dispatch is globally impossible. The master
        # kill-switch is the env TRADERSPOST_ENABLED (Fase 2 gate); if it's off,
        # no strategy can send real regardless of its own flags.
        "dry_run": settings.DRY_RUN or not settings.TRADERSPOST_ENABLED,
        "global_mode": global_mode,
    }


async def render(
    request: Request,
    template: str,
    context: dict | None = None,
    db: AsyncSession | None = None,
) -> HTMLResponse:
    """Render a full page, merging base context with page context."""
    ctx = await base_context(request, db)
    if context:
        ctx.update(context)
    return templates.TemplateResponse(request, template, ctx)


def redirect(path: str, flash: str | None = None, category: str = "success") -> RedirectResponse:
    """303 redirect after a POST. Optional flash passed via query string."""
    if flash:
        sep = "&" if "?" in path else "?"
        path = f"{path}{sep}flash={flash}&flash_cat={category}"
    return RedirectResponse(url=path, status_code=303)


def flash_messages(request: Request) -> list[tuple[str, str]]:
    """Read flash message from query params into the (category, message) form
    that base.html expects."""
    msg = request.query_params.get("flash")
    if not msg:
        return []
    cat = request.query_params.get("flash_cat", "success")
    return [(cat, msg)]
