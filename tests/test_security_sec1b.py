"""LOTE SEC-1b (Tarea 5) — el token del webhook fuera del query string.

Store efímero de un solo uso: el redirect lleva solo el id; la página destino
pide el token por fetch. Leerlo lo destruye; expira a los 60s; requiere sesión.
"""
import re
import time

import pytest
from httpx import AsyncClient

from app.core import token_once
from app.core.auth import (
    SESSION_COOKIE_NAME, create_session_token, generate_password_hash,
)
from app.core.config import settings
from app.models.strategy import Strategy

_USER = "admin"
_SECRET = "test_session_secret_0123456789abcdef"


@pytest.fixture(autouse=True)
def _cfg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "UI_USERNAME", _USER)
    monkeypatch.setattr(settings, "UI_PASSWORD", generate_password_hash("x"))
    monkeypatch.setattr(settings, "SESSION_SECRET", _SECRET)
    monkeypatch.setattr(settings, "APP_ENV", "development")


def _auth(client: AsyncClient) -> None:
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token(_USER))


# ---------------------------------------------------------------------------
# Store efímero (unidad)
# ---------------------------------------------------------------------------

def test_store_un_solo_read():
    tid = token_once.put("SECRETO123")
    assert token_once.take(tid) == "SECRETO123"
    assert token_once.take(tid) is None          # segundo read: agotado


def test_store_expira():
    tid = token_once.put("SECRETO123")
    token_once._store[tid] = ("SECRETO123", time.time() - 1)   # forzar expiración
    assert token_once.take(tid) is None


# ---------------------------------------------------------------------------
# Endpoint token-once
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_token_once_endpoint_un_solo_read(client: AsyncClient) -> None:
    _auth(client)
    tid = token_once.put("TOK_ABC")
    r = await client.get(f"/ui/strategies/token-once/{tid}")
    assert r.status_code == 200 and r.json()["token"] == "TOK_ABC"
    # segundo read → 410 (agotado)
    r2 = await client.get(f"/ui/strategies/token-once/{tid}")
    assert r2.status_code == 410


@pytest.mark.asyncio
async def test_token_once_requiere_sesion(client: AsyncClient) -> None:
    tid = token_once.put("TOK_XYZ")
    r = await client.get(f"/ui/strategies/token-once/{tid}")   # sin cookie
    assert r.status_code == 303                                # redirige a login
    assert r.headers["location"] == "/ui/login"
    assert token_once.take(tid) == "TOK_XYZ"                   # NO se consumió


# ---------------------------------------------------------------------------
# Flujos: alta y rotación — el token NO aparece en la Location
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_alta_no_lleva_token_en_url(client: AsyncClient, db) -> None:
    _auth(client)
    r = await client.post("/ui/strategies/new", data={
        "strategy_id": "SEC1B_A", "name": "A", "asset_symbol": "MES",
        "timeframe": "5m"})
    assert r.status_code == 303
    loc = r.headers["location"]
    assert "token_id=" in loc
    tid = re.search(r"token_id=([\w-]+)", loc).group(1)
    tok = (await client.get(f"/ui/strategies/token-once/{tid}")).json()["token"]
    assert tok and tok not in loc                # el secreto NO va en la URL


@pytest.mark.asyncio
async def test_rotacion_no_lleva_token_en_url(client: AsyncClient, db) -> None:
    _auth(client)
    db.add(Strategy(strategy_id="SEC1B_R", name="R", asset_symbol="MES",
                    status="paper", enabled=True))
    await db.commit()
    r = await client.post("/ui/strategies/SEC1B_R/regenerate-token")
    assert r.status_code == 303
    loc = r.headers["location"]
    assert "token_id=" in loc
    tid = re.search(r"token_id=([\w-]+)", loc).group(1)
    tok = (await client.get(f"/ui/strategies/token-once/{tid}")).json()["token"]
    assert tok and tok not in loc                # el secreto real NO va en la URL
