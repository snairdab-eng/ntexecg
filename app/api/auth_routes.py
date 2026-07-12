"""Login / logout routes. NOT protected by require_auth.

GET  /ui/login  → login form
POST /ui/login  → verify credentials → set httponly cookie → redirect /ui
POST /ui/logout → clear cookie → redirect /ui/login

Failed logins are written to AuditLog (action=LOGIN_FAILED). The plaintext
password is never logged or stored.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import login_guard
from app.core.auth import (
    SESSION_COOKIE_NAME,
    SESSION_TTL_HOURS,
    authenticate,
    create_session_token,
    revoke_all_sessions,
    verify_session_token,
)
from app.core.config import settings
from app.db.session import get_db
from app.services.audit_service import AuditService
from app.web.common import templates

router = APIRouter()


def _is_production() -> bool:
    return settings.APP_ENV == "production"


def _login_error(request: Request, msg: str, code: int):
    return templates.TemplateResponse(
        request, "login.html",
        {"app_name": settings.APP_NAME, "app_version": settings.APP_VERSION,
         "error": msg},
        status_code=code,
    )


@router.get("/ui/login", response_class=HTMLResponse)
async def login_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "login.html",
        {"app_name": settings.APP_NAME, "app_version": settings.APP_VERSION,
         "error": None,
         # LX-1 #1 — 2FA honesto: el campo TOTP solo se pinta si hay secreto.
         "totp_enabled": bool(settings.UI_TOTP_SECRET)},
    )


@router.post("/ui/login")
async def login_submit(
    request: Request,
    db: AsyncSession = Depends(get_db),
    username: str = Form(...),
    password: str = Form(...),
    totp: str = Form(""),
):
    ip = login_guard.client_ip(request)

    # SEC-1 Tarea 1 — lockout: si IP o usuario están bloqueados, 429 genérico
    # (no revela si el usuario existe).
    if login_guard.lock_remaining(ip, username) > 0:
        await AuditService().log(
            db, actor="anonymous", action="LOGIN_LOCKOUT", object_type="System",
            object_id="ui_login", reason="rate_limited", ip=ip)
        await db.commit()
        logger.warning("login_lockout ip={}", ip)
        return _login_error(
            request, "Demasiados intentos. Intenta más tarde.", 429)

    if not authenticate(username, password, totp):
        login_guard.record_failure(ip, username)
        # Backoff exponencial: solo se DUERME en producción (no en dev/test).
        delay = login_guard.backoff_seconds(ip, username)
        if delay and settings.APP_ENV == "production":
            import asyncio
            await asyncio.sleep(min(delay, 30.0))
        locked_now = login_guard.lock_remaining(ip, username) > 0
        await AuditService().log(
            db, actor="anonymous",
            action="LOGIN_LOCKOUT" if locked_now else "LOGIN_FAILED",
            object_type="System", object_id="ui_login",
            reason="rate_limited" if locked_now else "invalid_credentials",
            ip=ip)
        await db.commit()
        logger.warning("login_failed username={} ip={} locked={}",
                       username, ip, locked_now)
        if locked_now:
            return _login_error(
                request, "Demasiados intentos. Intenta más tarde.", 429)
        return _login_error(request, "Usuario o contraseña incorrectos.", 401)

    # Success → reset el contador, emite cookie de sesión, audita LOGIN_OK.
    login_guard.record_success(ip, username)
    token = create_session_token(username)
    await AuditService().log(
        db, actor=username, action="LOGIN_OK", object_type="System",
        object_id="ui_login", ip=ip,
    )
    await db.commit()
    logger.info("login_ok username={} ip={}", username, ip)

    response = RedirectResponse(url="/ui", status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_TTL_HOURS * 3600,
        httponly=True,
        samesite="lax",
        secure=_is_production(),
        path="/",
    )
    return response


@router.post("/ui/logout")
async def logout(request: Request) -> RedirectResponse:
    response = RedirectResponse(url="/ui/login", status_code=303)
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=_is_production(),
    )
    return response


@router.post("/ui/logout-all")
async def logout_all(
    request: Request, db: AsyncSession = Depends(get_db)
) -> RedirectResponse:
    """SEC-1 Tarea 6 — revoca TODAS las sesiones emitidas hasta ahora (un token
    robado deja de valer). Best-effort en memoria (se pierde al reiniciar; el
    reinicio también revoca globalmente). El router de auth es exento, así que
    verifico la sesión a mano."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    user = verify_session_token(token) if token else None
    if not user:
        return RedirectResponse(url="/ui/login", status_code=303)
    revoke_all_sessions()
    await AuditService().log(
        db, actor=user, action="LOGOUT_ALL", object_type="System",
        object_id="ui_login", ip=login_guard.client_ip(request))
    await db.commit()
    response = RedirectResponse(url="/ui/login", status_code=303)
    response.delete_cookie(
        key=SESSION_COOKIE_NAME, path="/", httponly=True,
        samesite="lax", secure=_is_production())
    return response
