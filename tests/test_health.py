import pytest
from httpx import AsyncClient

from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings


@pytest.mark.asyncio
async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data
    assert "env" in data


@pytest.mark.asyncio
async def test_ui_dashboard_redirects_without_auth(client: AsyncClient) -> None:
    """The dashboard is now protected — unauthenticated requests redirect to login."""
    response = await client.get("/ui")
    assert response.status_code == 303
    assert response.headers["location"] == "/ui/login"


@pytest.mark.asyncio
async def test_ui_dashboard_returns_200_when_authenticated(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_session_secret_health")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))
    response = await client.get("/ui")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
