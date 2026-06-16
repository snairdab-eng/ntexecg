"""Web UI authentication tests.

Uses the unauthenticated `client` fixture (no session cookie) so we can
exercise the login flow itself. Auth config is monkeypatched to known values.
"""
import pytest
from httpx import AsyncClient

from app.core.auth import (
    SESSION_COOKIE_NAME,
    create_session_token,
    generate_password_hash,
)
from app.core.config import settings

_TEST_USER = "admin"
_TEST_PASS = "testpass123"
# Hash once at import (bcrypt is slow) — reused across tests
_TEST_HASH = generate_password_hash(_TEST_PASS)
_TEST_SECRET = "test_session_secret_0123456789abcdef"


@pytest.fixture(autouse=True)
def _auth_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "UI_USERNAME", _TEST_USER)
    monkeypatch.setattr(settings, "UI_PASSWORD", _TEST_HASH)
    monkeypatch.setattr(settings, "SESSION_SECRET", _TEST_SECRET)
    monkeypatch.setattr(settings, "APP_ENV", "development")  # cookie secure=False


# ---------------------------------------------------------------------------
# Exempt routes (no auth needed)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_login_page_no_auth(client: AsyncClient) -> None:
    resp = await client.get("/ui/login")
    assert resp.status_code == 200
    assert "NTEXECG" in resp.text


@pytest.mark.asyncio
async def test_health_no_auth(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_webhook_not_redirected_to_login(client: AsyncClient) -> None:
    """Webhooks are exempt — a bad token returns 401, NOT a login redirect."""
    resp = await client.post(
        "/webhooks/luxalgo/test?token=wrong",
        json={"ticker": "MES", "action": "buy", "sentiment": "long"},
    )
    assert resp.status_code == 401              # webhook handled it
    assert resp.status_code != 303              # not an auth redirect


# ---------------------------------------------------------------------------
# Protected routes redirect when unauthenticated
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dashboard_redirects_when_unauthenticated(client: AsyncClient) -> None:
    resp = await client.get("/ui")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/ui/login"


@pytest.mark.asyncio
async def test_strategies_redirects_when_unauthenticated(client: AsyncClient) -> None:
    resp = await client.get("/ui/strategies")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/ui/login"


# ---------------------------------------------------------------------------
# Login flow
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_login_wrong_credentials(client: AsyncClient) -> None:
    resp = await client.post("/ui/login", data={"username": "admin", "password": "nope"})
    assert resp.status_code == 401
    assert "incorrectos" in resp.text.lower()
    # No session cookie issued
    assert SESSION_COOKIE_NAME not in resp.cookies


@pytest.mark.asyncio
async def test_login_correct_sets_cookie_and_redirects(client: AsyncClient) -> None:
    resp = await client.post(
        "/ui/login", data={"username": _TEST_USER, "password": _TEST_PASS}
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/ui"
    assert SESSION_COOKIE_NAME in resp.cookies


@pytest.mark.asyncio
async def test_authenticated_access_after_login(client: AsyncClient) -> None:
    # Log in — httpx stores the Set-Cookie in the client jar
    login = await client.post(
        "/ui/login", data={"username": _TEST_USER, "password": _TEST_PASS}
    )
    assert login.status_code == 303

    resp = await client.get("/ui")
    assert resp.status_code == 200
    assert "Dashboard" in resp.text or "NTEXECG" in resp.text


@pytest.mark.asyncio
async def test_valid_session_cookie_grants_access(client: AsyncClient) -> None:
    token = create_session_token(_TEST_USER)
    client.cookies.set(SESSION_COOKIE_NAME, token)
    resp = await client.get("/ui")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_invalid_cookie_redirects(client: AsyncClient) -> None:
    client.cookies.set(SESSION_COOKIE_NAME, "garbage.token.value")
    resp = await client.get("/ui")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/ui/login"


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_logout_clears_cookie(client: AsyncClient) -> None:
    await client.post("/ui/login", data={"username": _TEST_USER, "password": _TEST_PASS})
    resp = await client.post("/ui/logout")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/ui/login"


# ---------------------------------------------------------------------------
# Failed login is audited
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_failed_login_writes_audit(client: AsyncClient, db) -> None:
    from sqlalchemy import select
    from app.models.audit_log import AuditLog

    await client.post("/ui/login", data={"username": "admin", "password": "wrong"})
    result = await db.execute(
        select(AuditLog).where(AuditLog.action == "LOGIN_FAILED")
    )
    assert result.scalar_one_or_none() is not None
