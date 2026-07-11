"""Authentication primitives: bcrypt password hashing + signed JWT sessions.

Single admin user. Credentials live in .env (UI_USERNAME + bcrypt UI_PASSWORD).
Session is a signed JWT with 8-hour expiry, stored in an httponly cookie.
Secrets are read from settings at call time so tests can monkeypatch them.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from loguru import logger

from app.core.config import settings

SESSION_COOKIE_NAME = "ntexecg_session"
SESSION_TTL_HOURS = 8
_JWT_ALG = "HS256"

# SEC-1 Tarea 6 — revocación de sesiones (best-effort en memoria): watermark
# epoch; toda sesión con iat anterior queda inválida. Se pierde al reiniciar
# (reiniciar el servicio = revocación global — esa es la garantía dura).
_sessions_valid_from: float = 0.0


def revoke_all_sessions() -> None:
    """Invalida TODAS las sesiones emitidas hasta ahora (logout-all)."""
    global _sessions_valid_from
    _sessions_valid_from = datetime.now(timezone.utc).timestamp()


def _reset_revocation() -> None:
    """Solo para tests: limpia el watermark."""
    global _sessions_valid_from
    _sessions_valid_from = 0.0


def generate_password_hash(plain: str) -> str:
    """Return a bcrypt hash of the plaintext password."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Check a plaintext password against a bcrypt hash. Never raises."""
    if not plain or not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        # Malformed hash → treat as non-match rather than crash
        return False


def create_session_token(username: str) -> str:
    """Create a signed JWT session token with an 8-hour expiry."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": username,
        "iat": now,
        "exp": now + timedelta(hours=SESSION_TTL_HOURS),
        "jti": secrets.token_urlsafe(8),      # SEC-1 Tarea 6 — id de sesión
    }
    return jwt.encode(payload, settings.SESSION_SECRET, algorithm=_JWT_ALG)


def verify_session_token(token: str) -> str | None:
    """Return the username if the token is valid and unexpired, else None."""
    if not token or not settings.SESSION_SECRET:
        return None
    try:
        payload = jwt.decode(token, settings.SESSION_SECRET, algorithms=[_JWT_ALG])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None
    # SEC-1 Tarea 6 — rechaza sesiones anteriores al watermark de revocación.
    if _sessions_valid_from and int(payload.get("iat", 0)) < int(_sessions_valid_from):
        return None
    username = payload.get("sub")
    return username if isinstance(username, str) and username else None


def authenticate(username: str, password: str, totp_code: str = "") -> bool:
    """Validate a login against the configured admin credentials (+ TOTP si
    UI_TOTP_SECRET está configurado — SEC-1 Tarea 2; vacío = 2FA apagado)."""
    if not settings.UI_USERNAME or not settings.UI_PASSWORD:
        logger.warning("auth_not_configured — UI_USERNAME/UI_PASSWORD missing")
        return False
    if username != settings.UI_USERNAME:
        return False
    if not verify_password(password, settings.UI_PASSWORD):
        return False
    secret = settings.UI_TOTP_SECRET
    if secret:
        from app.core.totp import verify_totp
        if not verify_totp(secret, totp_code):
            return False
    return True
