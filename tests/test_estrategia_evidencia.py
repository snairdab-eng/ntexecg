"""LOTE EST-1 — 'probar ahora' READ-ONLY del scorer de Nivel 4.

NOTA (Parte C): la VISIBILIDAD de EST-1/EST-2 en la ficha (régimen 1h actual,
última evaluación, veredictos del Lab) se retiró del Config junto con los
formularios de filtros/régimen — su ausencia se verifica en test_parte_c.py.
El endpoint read-only `/probar-filtros` sigue VIVO (lo usa el Lab / diagnóstico)
y su semántica se mantiene bajo prueba aquí.
"""
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile

UTC = timezone.utc
SID = "ES5m_Evidencia"


@pytest.fixture(autouse=True)
def _auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_secret_evidencia")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))


class _MockMD:
    """MarketDataService falso: devuelve barras por timeframe."""
    def __init__(self, bars_by_tf: dict) -> None:
        self._b = bars_by_tf

    async def get_bars(self, symbol: str, timeframe: str, limit: int = 300):
        return list(self._b.get(timeframe, []))[-limit:]


def _bars_flat_vol(n: int, last_vol: float = 100.0) -> list[dict]:
    out = [{"close": 100.0, "high": 100.5, "low": 99.5, "volume": 100.0}
           for _ in range(n)]
    out[-1]["volume"] = last_vol
    return out


async def _seed(db: AsyncSession) -> None:
    db.add(Strategy(strategy_id=SID, name="Ev", asset_symbol="MES",
                    status="paper", enabled=True))
    db.add(StrategyProfile(strategy_id=SID, pipeline_config_json={}))
    await db.commit()


# ---------------------------------------------------------------------------
# Probar ahora (read-only) — endpoint vivo
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_probar_ahora_score(client: AsyncClient, app,
                                  db: AsyncSession) -> None:
    """volume_relative sobre 30 barras de volumen uniforme → ratio 1.0 →
    subscore 0.5 → score 50 (determinista, sin depender de la hora)."""
    await _seed(db)
    app.state.market_data = _MockMD({"5m": _bars_flat_vol(30)})
    r = await client.get(
        f"/ui/strategies/{SID}/probar-filtros"
        "?f_volume_relative_enabled=1&f_volume_relative_weight=1")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["score"] == 50
    assert j["measured"] is True
    assert j["passed"] is False                          # 50 < 70
    assert j["n_bars"] == 30
    names = [f["name"] for f in j["filters"]]
    assert "volume_relative" in names


@pytest.mark.asyncio
async def test_probar_ahora_sin_filtros_100(client: AsyncClient, app,
                                            db: AsyncSession) -> None:
    await _seed(db)
    app.state.market_data = _MockMD({"5m": _bars_flat_vol(30)})
    r = await client.get(f"/ui/strategies/{SID}/probar-filtros")
    assert r.status_code == 200
    j = r.json()
    assert j["score"] == 100 and j["measured"] is False   # passthrough


@pytest.mark.asyncio
async def test_probar_ahora_sin_bridge_409(client: AsyncClient,
                                           db: AsyncSession) -> None:
    """Sin market data cableada (app.state sin market_data) → 409 con aviso,
    nunca un número inventado."""
    await _seed(db)
    r = await client.get(
        f"/ui/strategies/{SID}/probar-filtros?f_volume_relative_enabled=1")
    assert r.status_code == 409
    assert "bridge" in r.json()["error"].lower()


@pytest.mark.asyncio
async def test_probar_ahora_bridge_sin_barras_409(client: AsyncClient, app,
                                                  db: AsyncSession) -> None:
    await _seed(db)
    app.state.market_data = _MockMD({"5m": []})           # bridge sin barras
    r = await client.get(
        f"/ui/strategies/{SID}/probar-filtros?f_volume_relative_enabled=1")
    assert r.status_code == 409
    assert "barras" in r.json()["error"].lower()
