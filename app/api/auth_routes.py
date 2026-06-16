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

from app.core.auth import (
    SESSION_COOKIE_NAME,
    SESSION_TTL_HOURS,
    authenticate,
    create_session_token,
)
from app.core.config import settings
from app.db.session import get_db
from app.services.audit_service import AuditService
from app.web.common import templates

router = APIRouter()


def _is_production() -> bool:
    return settings.APP_ENV == "production"


@router.get("/ui/login", response_class=HTMLResponse)
async def login_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "login.html",
        {"app_name": settings.APP_NAME, "app_version": settings.APP_VERSION, "error": None},
    )


@router.post("/ui/login")
async def login_submit(
    request: Request,
    db: AsyncSession = Depends(get_db),
    username: str = Form(...),
    password: str = Form(...),
):
    client_ip = request.client.host if request.client else None

    if not authenticate(username, password):
        # Audit the failed attempt (never log the password)
        await AuditService().log(
            db, actor=username or "unknown", action="LOGIN_FAILED",
            object_type="System", object_id="ui_login",
            reason="invalid_credentials", ip=client_ip,
        )
        await db.commit()
        logger.warning("login_failed username={} ip={}", username, client_ip)
        # Stay on the login page with an error, HTTP 401
        return templates.TemplateResponse(
            request, "login.html",
            {
                "app_name": settings.APP_NAME, "app_version": settings.APP_VERSION,
                "error": "Usuario o contraseña incorrectos.",
            },
            status_code=401,
        )

    # Success → issue session cookie + redirect to dashboard
    token = create_session_token(username)
    await AuditService().log(
        db, actor=username, action="LOGIN", object_type="System",
        object_id="ui_login", ip=client_ip,
    )
    await db.commit()
    logger.info("login_ok username={} ip={}", username, client_ip)

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
