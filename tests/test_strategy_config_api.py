"""Tests — API de config por estrategia: efectiva (inherited/override/effective),
calibración, y scale-entry (diseño, rechaza enabled)."""
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings
from app.models.asset_profile import AssetProfile
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile


@pytest.fixture(autouse=True)
def _auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_secret_cfg")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))


async def _seed(db: AsyncSession) -> None:
    db.add(AssetProfile(symbol="MES", name="Micro S&P", contract_type="futures_micro", active=True,
                        session_config_json={"timezone": "America/New_York", "days_enabled": [1, 2, 3, 4, 5],
                                             "entry_start": "09:30", "entry_end": "15:45", "next_day_end": False},
                        sl_atr_multiplier=2.0))
    db.add(Strategy(strategy_id="ES5m", name="MicroES5m", asset_symbol="MES", status="paper", enabled=True))
    await db.commit()


@pytest.mark.asyncio
async def test_get_config_layers(client: AsyncClient, db: AsyncSession) -> None:
    await _seed(db)
    r = await client.get("/api/strategies/ES5m/config")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["asset_symbol"] == "MES" and j["status"] == "paper"
    assert j["inherited"]["sl_atr_multiplier"] == 2.0          # del activo
    assert j["inherited"]["window"]["start"] == "09:30"
    assert j["override"]["windows"] is None                     # aún sin override
    # efectivo: sin override de estrategia, hereda 2.0 del activo
    assert j["effective"]["sl_atr_multiplier"] == 2.0


@pytest.mark.asyncio
async def test_patch_calibration_overrides(client: AsyncClient, db: AsyncSession) -> None:
    await _seed(db)
    body = {"sl_atr_multiplier": 2.5, "atr_timeframe": "5m",
            "windows": [{"days": [1, 2, 3, 4, 5], "start": "09:20", "end": "15:45", "next_day_end": False}]}
    r = await client.patch("/api/strategies/ES5m/calibration", json=body)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["override"]["sl_atr_multiplier"] == 2.5
    assert j["override"]["windows"][0]["start"] == "09:20"
    assert j["effective"]["sl_atr_multiplier"] == 2.5          # estrategia override al activo
    assert j["effective"]["window"]["start"] == "09:20"
    # persistido en StrategyProfile.pipeline_config_json
    p = (await db.execute(select(StrategyProfile).where(StrategyProfile.strategy_id == "ES5m"))).scalar_one()
    assert p.pipeline_config_json["windows"][0]["start"] == "09:20"


@pytest.mark.asyncio
async def test_calibration_invalid_tf(client: AsyncClient, db: AsyncSession) -> None:
    await _seed(db)
    r = await client.patch("/api/strategies/ES5m/calibration", json={"atr_timeframe": "7m"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_scale_entry_design_saved(client: AsyncClient, db: AsyncSession) -> None:
    await _seed(db)
    body = {"mode": "design_only", "levels": [0.75, 1.25], "quantities": [0, 1, 4],
            "max_micro_contracts": 5, "stop_mode": "common_position_stop"}
    r = await client.patch("/api/strategies/ES5m/scale-entry", json=body)
    assert r.status_code == 200, r.text
    se = r.json()["scale_entry"]
    assert se["mode"] == "design_only" and se["levels"] == [0.75, 1.25]
    assert se["quantities"] == [0, 1, 4] and se["max_micro_contracts"] == 5
    # visible en /config
    cfg = (await client.get("/api/strategies/ES5m/config")).json()
    assert cfg["scale_entry"]["max_micro_contracts"] == 5


@pytest.mark.asyncio
async def test_scale_entry_rejects_enabled(client: AsyncClient, db: AsyncSession) -> None:
    await _seed(db)
    r = await client.patch("/api/strategies/ES5m/scale-entry",
                           json={"mode": "enabled", "levels": [1], "quantities": [1]})
    assert r.status_code == 422
    assert "enabled" in r.text


@pytest.mark.asyncio
async def test_scale_entry_off_removes(client: AsyncClient, db: AsyncSession) -> None:
    await _seed(db)
    await client.patch("/api/strategies/ES5m/scale-entry",
                       json={"mode": "design_only", "levels": [1], "quantities": [1], "max_micro_contracts": 2})
    r = await client.patch("/api/strategies/ES5m/scale-entry", json={"mode": "off"})
    assert r.status_code == 200
    assert r.json()["scale_entry"] is None


@pytest.mark.asyncio
async def test_detail_page_renders(client: AsyncClient, db: AsyncSession) -> None:
    await _seed(db)
    db.add(StrategyProfile(strategy_id="ES5m"))  # profile presente => tab Config visible
    await db.commit()
    r = await client.get("/ui/strategies/ES5m")
    assert r.status_code == 200, r.text
    assert "Efectivo" not in r.text           # tab Efectivo eliminada
    assert "stratCfg" not in r.text           # JS del loader eliminado
    assert "Compras escalonadas" in r.text    # Scale Entry ahora en Config
    assert "/ui/strategies/ES5m/scale-entry" in r.text


@pytest.mark.asyncio
async def test_scale_entry_server_form_persists(client: AsyncClient, db: AsyncSession) -> None:
    await _seed(db)
    r = await client.post("/ui/strategies/ES5m/scale-entry", data={
        "scale_entry_mode": "design_only", "levels": "0.75, 1.25",
        "quantities": "0, 1, 4", "max_micro_contracts": "5"})
    assert r.status_code == 303
    p = (await db.execute(select(StrategyProfile).where(StrategyProfile.strategy_id == "ES5m"))).scalar_one()
    await db.refresh(p)
    se = p.pipeline_config_json["scale_entry"]
    assert se["levels"] == [0.75, 1.25] and se["quantities"] == [0, 1, 4]
    assert se["max_micro_contracts"] == 5 and se["mode"] == "design_only"
    assert se["stop_mode"] == "common_position_stop"


@pytest.mark.asyncio
async def test_scale_entry_server_rejects_enabled(client: AsyncClient, db: AsyncSession) -> None:
    await _seed(db)
    r = await client.post("/ui/strategies/ES5m/scale-entry", data={"scale_entry_mode": "enabled"})
    assert r.status_code == 303
    assert "error" in r.headers["location"]
