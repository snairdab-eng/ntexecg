"""Authentication primitives: bcrypt password hashing + signed JWT sessions.

Single admin user. Credentials live in .env (UI_USERNAME + bcrypt UI_PASSWORD).
Session is a signed JWT with 8-hour expiry, stored in an httponly cookie.
Secrets are read from settings at call time so tests can monkeypatch them.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from loguru import logger

from app.core.config import settings

SESSION_COOKIE_NAME = "ntexecg_session"
SESSION_TTL_HOURS = 8
_JWT_ALG = "HS256"


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
    username = payload.get("sub")
    return username if isinstance(username, str) and username else None


def authenticate(username: str, password: str) -> bool:
    """Validate a login against the configured admin credentials."""
    if not settings.UI_USERNAME or not settings.UI_PASSWORD:
        logger.warning("auth_not_configured — UI_USERNAME/UI_PASSWORD missing")
        return False
    # Constant-ish: always run bcrypt even on username mismatch is overkill here;
    # username compare first is fine for a single-user admin panel.
    if username != settings.UI_USERNAME:
        return False
    return verify_password(password, settings.UI_PASSWORD)
