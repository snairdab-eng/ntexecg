"""LOTE EST-1 — evidencia en vivo del Nivel 4 (que se VEA que funcionan).

Candados verificados:
  - régimen 1h ACTUAL visible con barras suficientes/insuficientes (mock);
  - última evaluación real (score/umbral/motivo) con y sin decisiones;
  - 'probar ahora' READ-ONLY: score correcto sobre barras mock; sin bridge → 409.
Todo es VISIBILIDAD: no cambia la semántica del pipeline.
"""
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings
from app.models.normalized_signal import NormalizedSignal
from app.models.decision import StrategyDecision
from app.models.raw_signal import RawSignal
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile

UTC = timezone.utc
SID = "ES5m_Evidencia"


@pytest.fixture(autouse=True)
def _auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_secret_evidencia")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))


@pytest.fixture(autouse=True)
def _clear_regime_cache() -> None:
    # La caché TTL del régimen es de módulo (intencional en prod); aislarla
    # entre tests para no arrastrar un régimen de un test al siguiente.
    import app.web.routes_strategies as rs
    rs._regime_cache.clear()


class _MockMD:
    """MarketDataService falso: devuelve barras por timeframe."""
    def __init__(self, bars_by_tf: dict) -> None:
        self._b = bars_by_tf

    async def get_bars(self, symbol: str, timeframe: str, limit: int = 300):
        return list(self._b.get(timeframe, []))[-limit:]


def _bars_trend_up(n: int) -> list[dict]:
    return [{"close": 100.0 + i, "high": 100.0 + i, "low": 100.0 + i,
             "volume": 100.0} for i in range(n)]


def _bars_flat_vol(n: int, last_vol: float = 100.0) -> list[dict]:
    out = [{"close": 100.0, "high": 100.5, "low": 99.5, "volume": 100.0}
           for _ in range(n)]
    out[-1]["volume"] = last_vol
    return out


async def _seed(db: AsyncSession, *, regime_enabled: bool = True,
                filters: dict | None = None) -> None:
    cfg: dict = {}
    if regime_enabled:
        cfg["regime"] = {"enabled": True, "timeframe": "1h",
                         "allowed_regimes": ["trending_bull"]}
    if filters:
        cfg["filters"] = filters
    db.add(Strategy(strategy_id=SID, name="Ev", asset_symbol="MES",
                    status="paper", enabled=True))
    db.add(StrategyProfile(strategy_id=SID, pipeline_config_json=cfg))
    await db.commit()


async def _decision(db: AsyncSession, *, score: int, outcome: str,
                    quality: str = "MEDIUM", block_reason=None) -> None:
    raw = RawSignal(source="luxalgo", strategy_id=SID, payload_json={},
                    token_valid=True)
    db.add(raw)
    await db.flush()
    norm = NormalizedSignal(raw_signal_id=raw.id, strategy_id=SID,
                            ticker_received="MES", action="buy",
                            sentiment="long", signal_ts=datetime.now(UTC),
                            dedupe_key=raw.id.hex)
    db.add(norm)
    await db.flush()
    db.add(StrategyDecision(
        normalized_signal_id=norm.id, strategy_id=SID, outcome=outcome,
        score=score, block_reason=block_reason,
        pipeline_execution_json={"level_4": {"score": score, "passed":
                                             outcome != "BLOCK",
                                             "filters_active": True,
                                             "quality": quality}},
        config_snapshot_json={"score_minimum": 70},
    ))
    await db.commit()


# ---------------------------------------------------------------------------
# Régimen 1h actual (mock provider)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_regime_now_suficiente(client: AsyncClient, app,
                                     db: AsyncSession) -> None:
    await _seed(db)
    app.state.market_data = _MockMD({"1h": _bars_trend_up(40)})
    r = await client.get(f"/ui/strategies/{SID}")
    assert r.status_code == 200
    html = r.text
    assert "régimen 1h actual" in html
    assert "tendencia alcista" in html
    assert "ER" in html and "barras" in html         # evidencia numérica


@pytest.mark.asyncio
async def test_regime_now_insuficiente(client: AsyncClient, app,
                                       db: AsyncSession) -> None:
    await _seed(db)
    app.state.market_data = _MockMD({"1h": _bars_trend_up(10)})   # N<20
    r = await client.get(f"/ui/strategies/{SID}")
    assert r.status_code == 200
    assert "barras insuficientes" in r.text
    assert "unknown" in r.text


# ---------------------------------------------------------------------------
# Última evaluación (con y sin decisiones)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ultima_eval_con_decision(client: AsyncClient, db: AsyncSession) -> None:
    await _seed(db, regime_enabled=False)
    await _decision(db, score=84, outcome="APPROVE", quality="HIGH")
    r = await client.get(f"/ui/strategies/{SID}")
    assert r.status_code == 200
    html = r.text
    assert "score 84" in html and "umbral 70" in html
    assert "calidad HIGH" in html


@pytest.mark.asyncio
async def test_ultima_eval_bloqueada(client: AsyncClient, db: AsyncSession) -> None:
    await _seed(db, regime_enabled=False)
    await _decision(db, score=52, outcome="BLOCK", quality="LOW",
                    block_reason="score_below_minimum")
    html = (await client.get(f"/ui/strategies/{SID}")).text
    assert "score 52" in html
    assert "bloqueada" in html and "score_below_minimum" in html


@pytest.mark.asyncio
async def test_ultima_eval_sin_decisiones(client: AsyncClient, db: AsyncSession) -> None:
    await _seed(db, regime_enabled=False)                # sin filtros activos
    html = (await client.get(f"/ui/strategies/{SID}")).text
    assert "sin evaluaciones aún" in html
    assert "score 100 automático" in html               # filtros inactivos


# ---------------------------------------------------------------------------
# Probar ahora (read-only)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_probar_ahora_score(client: AsyncClient, app,
                                  db: AsyncSession) -> None:
    """volume_relative sobre 30 barras de volumen uniforme → ratio 1.0 →
    subscore 0.5 → score 50 (determinista, sin depender de la hora)."""
    await _seed(db, regime_enabled=False)
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
    await _seed(db, regime_enabled=False)
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
    await _seed(db, regime_enabled=False)
    r = await client.get(
        f"/ui/strategies/{SID}/probar-filtros?f_volume_relative_enabled=1")
    assert r.status_code == 409
    assert "bridge" in r.json()["error"].lower()


@pytest.mark.asyncio
async def test_probar_ahora_bridge_sin_barras_409(client: AsyncClient, app,
                                                  db: AsyncSession) -> None:
    await _seed(db, regime_enabled=False)
    app.state.market_data = _MockMD({"5m": []})           # bridge sin barras
    r = await client.get(
        f"/ui/strategies/{SID}/probar-filtros?f_volume_relative_enabled=1")
    assert r.status_code == 409
    assert "barras" in r.json()["error"].lower()
