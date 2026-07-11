"""LOTE SEC-1 (núcleo 1,2,3,4,6) — endurecimiento del panel.

Rate-limit/lockout del login, 2FA TOTP opcional, fail-fast del SESSION_SECRET,
headers de seguridad + CSP (X-Frame SAMEORIGIN / frame-ancestors 'self' por la
enmienda del iframe del Lab), y revocación de sesiones (logout-all).
"""
import asyncio
import time

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings
from app.core.auth import generate_password_hash
from app.core.totp import _hotp
from app.models.audit_log import AuditLog
from app.models.strategy import Strategy

_USER = "admin"
_PASS = "testpass123"
_HASH = generate_password_hash(_PASS)
_SECRET = "test_session_secret_0123456789abcdef"     # 36 chars ≥ 32
_TOTP = "JBSWY3DPEHPK3PXP"                            # base32 válido


@pytest.fixture(autouse=True)
def _cfg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "UI_USERNAME", _USER)
    monkeypatch.setattr(settings, "UI_PASSWORD", _HASH)
    monkeypatch.setattr(settings, "SESSION_SECRET", _SECRET)
    monkeypatch.setattr(settings, "APP_ENV", "development")   # no fail-fast, no sleep
    monkeypatch.setattr(settings, "UI_TOTP_SECRET", "")       # 2FA off por default


# ---------------------------------------------------------------------------
# Tarea 1 — lockout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lockout_tras_10_fallos(client: AsyncClient, db) -> None:
    last = None
    for _ in range(10):
        last = await client.post("/ui/login",
                                 data={"username": "admin", "password": "wrong"})
    assert last.status_code == 429                    # el 10º cruza el umbral
    # un intento más sigue bloqueado (429) aunque la clave fuera correcta
    r = await client.post("/ui/login",
                          data={"username": "admin", "password": _PASS})
    assert r.status_code == 429
    lock = (await db.execute(select(AuditLog).where(
        AuditLog.action == "LOGIN_LOCKOUT"))).scalars().first()
    assert lock is not None


# ---------------------------------------------------------------------------
# Tarea 2 — 2FA TOTP on/off
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_totp_off_no_pide_codigo(client: AsyncClient) -> None:
    r = await client.post("/ui/login",
                          data={"username": _USER, "password": _PASS})
    assert r.status_code == 303                       # 2FA apagado = flujo actual


@pytest.mark.asyncio
async def test_totp_on_exige_codigo(
    client: AsyncClient, db, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "UI_TOTP_SECRET", _TOTP)
    # password correcto pero SIN código → falla
    r = await client.post("/ui/login",
                          data={"username": _USER, "password": _PASS})
    assert r.status_code == 401
    # código inválido → falla y cuenta para el lockout (queda LOGIN_FAILED)
    r = await client.post("/ui/login",
                          data={"username": _USER, "password": _PASS,
                                "totp": "000000"})
    assert r.status_code == 401
    assert (await db.execute(select(AuditLog).where(
        AuditLog.action == "LOGIN_FAILED"))).scalars().first() is not None
    # código válido del periodo actual → entra
    code = _hotp(_TOTP, int(time.time() // 30))
    r = await client.post("/ui/login",
                          data={"username": _USER, "password": _PASS,
                                "totp": code})
    assert r.status_code == 303 and SESSION_COOKIE_NAME in r.cookies


# ---------------------------------------------------------------------------
# Tarea 3 — fail-fast del SESSION_SECRET
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_failfast_secreto_corto_503(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "SESSION_SECRET", "corto")
    r = await client.get("/ui/login")
    assert r.status_code == 503 and "insegura" in r.text
    # con secreto fuerte, no bloquea
    monkeypatch.setattr(settings, "SESSION_SECRET", _SECRET)
    r = await client.get("/ui/login")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_failfast_permitido_en_test(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "test")
    monkeypatch.setattr(settings, "SESSION_SECRET", "corto")
    r = await client.get("/ui/login")
    assert r.status_code == 200                        # test permite corto


# ---------------------------------------------------------------------------
# Tarea 4 — headers de seguridad + CSP (+ enmienda del iframe del Lab)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_headers_seguridad_html(client: AsyncClient) -> None:
    r = await client.get("/ui/login")
    assert r.status_code == 200
    assert r.headers["x-frame-options"] == "SAMEORIGIN"      # NO DENY (L6 iframe)
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "same-origin"
    csp = r.headers["content-security-policy"]
    assert "frame-ancestors 'self'" in csp                   # enmienda del arquitecto
    assert "default-src 'self'" in csp


@pytest.mark.asyncio
async def test_lab_iframe_embebe_con_headers(client: AsyncClient, db) -> None:
    """Enmienda 1: la sub-pestaña Lab sigue embebiendo same-origin con los
    headers puestos (SAMEORIGIN + frame-ancestors 'self' lo permiten)."""
    db.add(Strategy(strategy_id="ES5m_Sec", name="Sec", asset_symbol="ES",
                    status="paper", enabled=True))
    await db.commit()
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token(_USER))
    r = await client.get("/ui/strategies/ES5m_Sec")
    assert r.status_code == 200
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    assert 'src="/ui/lab?strategy=ES5m_Sec"' in r.text        # embed intacto


# ---------------------------------------------------------------------------
# Tarea 6 — revocación de sesiones (logout-all)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_logout_all_invalida_tokens_previos(client: AsyncClient) -> None:
    token = create_session_token(_USER)
    client.cookies.set(SESSION_COOKIE_NAME, token)
    assert (await client.get("/ui")).status_code == 200      # válido antes

    await asyncio.sleep(1.1)                                  # iat < watermark
    client.cookies.set(SESSION_COOKIE_NAME, token)
    r = await client.post("/ui/logout-all")
    assert r.status_code == 303

    # el token viejo ya no vale (aunque la cookie siga)
    client.cookies.set(SESSION_COOKIE_NAME, token)
    r = await client.get("/ui")
    assert r.status_code == 303 and r.headers["location"] == "/ui/login"
    # una sesión NUEVA sí entra
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token(_USER))
    assert (await client.get("/ui")).status_code == 200
