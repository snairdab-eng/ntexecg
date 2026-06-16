"""Auth dependency for protecting web UI routes.

require_auth checks the session cookie and, on failure, raises NotAuthenticated,
which an app-level exception handler converts into a 303 redirect to /ui/login.

Exempt routes are simply NOT given this dependency:
  /health, /webhooks/*, /ui/login, /ui/logout, /static/*
"""
from __future__ import annotations

from fastapi import Request

from app.core.auth import SESSION_COOKIE_NAME, verify_session_token


class NotAuthenticated(Exception):
    """Raised by require_auth when there is no valid session."""


async def require_auth(request: Request) -> str:
    """FastAPI dependency: return the username for a valid session, else redirect.

    Applied at router-include level to all protected web routers.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    username = verify_session_token(token) if token else None
    if not username:
        raise NotAuthenticated()
    # Expose for downstream handlers/templates if needed
    request.state.username = username
    return username
