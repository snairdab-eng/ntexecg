"""Tests — Calibración por Activo (UI + API JSON) sobre el esquema real.

Cubre: render de la lista, API GET/PATCH de asset-profiles, validaciones,
confirmación cuando hay estrategias live, API de strategies + cambio de status,
y la advertencia de multi-estrategia por asset_symbol.
"""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings
from app.models.asset_profile import AssetProfile
from app.models.strategy import Strategy


@pytest.fixture(autouse=True)
def _auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_session_secret_ap")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))


async def _mk_asset(db: AsyncSession, symbol="MES", sl=2.0, tf="5m") -> AssetProfile:
    a = AssetProfile(
        symbol=symbol, name=f"{symbol} test", contract_type="futures_micro", active=True,
        session_config_json={"timezone": "America/New_York", "days_enabled": [1, 2, 3, 4, 5],
                             "entry_start": "09:30", "entry_end": "15:45", "next_day_end": False},
        sl_atr_multiplier=sl, atr_timeframe=tf,
    )
    db.add(a)
    await db.commit()
    return a


async def _mk_strategy(db, sid, symbol, status="candidate") -> Strategy:
    s = Strategy(strategy_id=sid, name=sid, asset_symbol=symbol, status=status, enabled=False)
    db.add(s)
    await db.commit()
    return s


@pytest.mark.asyncio
async def test_list_assets_shows_real_fields(client: AsyncClient, db: AsyncSession) -> None:
    await _mk_asset(db)
    r = await client.get("/ui/assets")
    assert r.status_code == 200
    assert "Calibración por Activo" in r.text
    assert "Tipo contrato" in r.text and "ATR TF" in r.text
    assert "MES" in r.text


@pytest.mark.asyncio
async def test_multi_strategy_warning(client: AsyncClient, db: AsyncSession) -> None:
    await _mk_asset(db)
    await _mk_strategy(db, "MES_a", "MES")
    await _mk_strategy(db, "MES_b", "MES")
    r = await client.get("/ui/assets")
    assert "estrategias" in r.text  # badge "⚠ 2 estrategias"


@pytest.mark.asyncio
async def test_api_list_and_get(client: AsyncClient, db: AsyncSession) -> None:
    a = await _mk_asset(db)
    r = await client.get("/api/asset-profiles")
    assert r.status_code == 200
    data = r.json()
    assert any(x["symbol"] == "MES" for x in data)
    r2 = await client.get(f"/api/asset-profiles/{a.id}")
    assert r2.status_code == 200
    assert r2.json()["symbol"] == "MES"


@pytest.mark.asyncio
async def test_api_patch_updates(client: AsyncClient, db: AsyncSession) -> None:
    a = await _mk_asset(db, sl=2.0, tf="5m")
    body = {"sl_atr_multiplier": 2.5, "atr_timeframe": "5m",
            "session": {"entry_start": "09:20", "entry_end": "15:45",
                        "days_enabled": [1, 2, 3, 4, 5], "next_day_end": False}}
    r = await client.patch(f"/api/asset-profiles/{a.id}", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["sl_atr_multiplier"] == 2.5
    assert out["window"]["start"] == "09:20"


@pytest.mark.asyncio
async def test_api_patch_validations(client: AsyncClient, db: AsyncSession) -> None:
    a = await _mk_asset(db)
    # sl <= 0 → 422 (pydantic gt=0)
    assert (await client.patch(f"/api/asset-profiles/{a.id}", json={"sl_atr_multiplier": 0})).status_code == 422
    # atr_timeframe inválido → 422
    assert (await client.patch(f"/api/asset-profiles/{a.id}", json={"atr_timeframe": "7m"})).status_code == 422
    # days vacío → 422
    r = await client.patch(f"/api/asset-profiles/{a.id}", json={"session": {"days_enabled": []}})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_api_patch_requires_confirm_when_live(client: AsyncClient, db: AsyncSession) -> None:
    a = await _mk_asset(db)
    await _mk_strategy(db, "MES_live", "MES", status="paper")
    # sin confirm → 409
    r = await client.patch(f"/api/asset-profiles/{a.id}", json={"sl_atr_multiplier": 3.0})
    assert r.status_code == 409
    # con confirm → 200
    r2 = await client.patch(f"/api/asset-profiles/{a.id}", json={"sl_atr_multiplier": 3.0, "confirm": True})
    assert r2.status_code == 200
    assert r2.json()["sl_atr_multiplier"] == 3.0


@pytest.mark.asyncio
async def test_api_strategies_by_symbol_and_status_patch(client: AsyncClient, db: AsyncSession) -> None:
    await _mk_asset(db)
    s = await _mk_strategy(db, "MES_x", "MES", status="candidate")
    r = await client.get("/api/strategies", params={"asset_symbol": "MES"})
    assert r.status_code == 200 and len(r.json()) == 1
    # status válido
    ok = await client.patch(f"/api/strategies/{s.id}/status", json={"status": "shadow"})
    assert ok.status_code == 200 and ok.json()["status"] == "shadow"
    # status inválido → 422
    bad = await client.patch(f"/api/strategies/{s.id}/status", json={"status": "production"})
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_ui_form_update_persists(client: AsyncClient, db: AsyncSession) -> None:
    await _mk_asset(db, symbol="MNQ", sl=2.0, tf="5m")
    r = await client.post("/ui/assets/MNQ", data={
        "form_full": "1", "active": "on", "entry_start": "18:00", "entry_end": "17:00",
        "days": ["0", "1", "2", "3", "4", "5"], "next_day_end": "on",
        "sl_atr_multiplier": "8.0", "atr_timeframe": "5m",
    })
    assert r.status_code == 303
    row = (await db.execute(select(AssetProfile).where(AssetProfile.symbol == "MNQ"))).scalar_one()
    await db.refresh(row)
    assert float(row.sl_atr_multiplier) == 8.0
    assert row.session_config_json["next_day_end"] is True
    assert row.session_config_json["entry_start"] == "18:00"


@pytest.mark.asyncio
async def test_ui_form_rejects_bad_sl(client: AsyncClient, db: AsyncSession) -> None:
    await _mk_asset(db, symbol="MGC")
    r = await client.post("/ui/assets/MGC", data={
        "entry_start": "09:30", "entry_end": "15:45", "days": ["1", "2"],
        "sl_atr_multiplier": "-1",
    })
    assert r.status_code == 303
    assert "error" in r.headers["location"] or "flash" in r.headers["location"]
