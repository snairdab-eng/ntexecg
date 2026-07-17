"""FILTROS-OFF — apagado del Nivel 4 (quality score + régimen HMM) en producción.

El N4 sigue en el código (passthrough honesto NX-04: sin filtros → score 100,
quality=UNKNOWN). Cubre: helpers de inventario/neutralización; config neutralizada
→ score NO bloquea (passthrough, UNKNOWN) aun con score_minimum estricto; régimen
no bloquea; exits intactos; y el alta ya no nace con la llave score_minimum.
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from httpx import AsyncClient

import scripts.inventario_l4 as inv
from app.models.normalized_signal import NormalizedSignal
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from app.services.filter_pipeline import FilterPipeline
from app.services.quality_scorer import QualityScorer, _NAMES


# ---------------------------------------------------------------------------
# 1) Helpers de inventario / neutralización
# ---------------------------------------------------------------------------

def test_l4_effective_bloquearia_vs_passthrough():
    f = _NAMES[0]
    assert inv.l4_effective(
        {"filters": {f: {"enabled": True, "weight": 1.0}}})["efecto"] == "bloquearía"
    assert inv.l4_effective(
        {"regime": {"enabled": True, "allowed_regimes": ["ranging"]}}
    )["efecto"] == "bloquearía"
    # regime.enabled SIN lista = no-op (NX-26) → passthrough
    assert inv.l4_effective({"regime": {"enabled": True}})["efecto"] == "passthrough"
    # filtro deshabilitado / peso 0 → passthrough
    assert inv.l4_effective(
        {"filters": {f: {"enabled": True, "weight": 0}}})["efecto"] == "passthrough"
    assert inv.l4_effective({})["efecto"] == "passthrough"


def test_neutralize_l4_quita_llaves_conserva_resto():
    pj = {"filters": {"x": 1}, "regime": {"enabled": True}, "score_minimum": 55,
          "scale_entry": {"mode": "execute"}, "windows": [{"a": 1}]}
    new, removed = inv.neutralize_l4(pj)
    assert set(removed) == {"filters", "regime", "score_minimum"}
    assert not any(k in new for k in ("filters", "regime", "score_minimum"))
    assert new["scale_entry"] == {"mode": "execute"} and new["windows"] == [{"a": 1}]
    # idempotente: una config ya neutralizada no retira nada
    _n2, rem2 = inv.neutralize_l4(new)
    assert rem2 == {}


@pytest.mark.asyncio
async def test_scorer_sin_filtros_es_100_passthrough():
    sig = _signal()
    assert await QualityScorer().score(sig, [], {}) == 100      # sin filtros → 100


# ---------------------------------------------------------------------------
# Pipeline — helpers
# ---------------------------------------------------------------------------

def _signal(action="buy", sentiment="long", price=5500.0) -> NormalizedSignal:
    return NormalizedSignal(
        raw_signal_id=uuid.uuid4(), strategy_id="filt_off", ticker_received="MES",
        mapped_symbol="MESU2025", action=action, sentiment=sentiment, price=price,
        signal_ts=datetime.now(timezone.utc), dedupe_key=uuid.uuid4().hex)


def _strategy() -> Strategy:
    return Strategy(strategy_id="filt_off", name="F", asset_symbol="MES",
                    status="live", enabled=True)


# ---------------------------------------------------------------------------
# 2) Config neutralizada → score NO bloquea (passthrough, quality=UNKNOWN)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_config_neutralizada_no_bloquea_score(db: AsyncSession, market_data_service):
    # sin filtros PERO con score_minimum estricto residual (100): el passthrough
    # (score 100) pasa igual — el N4 apagado nunca estrangula por score.
    config = {"mode": "normal", "score_minimum": 100}
    r = await FilterPipeline(market_data_service).evaluate(
        db, _signal(), _strategy(), config)
    assert r.outcome == "APPROVE"
    assert r.block_reason is None
    assert r.quality == "UNKNOWN"        # NX-04 — sin medición real
    assert r.filters_active is False


@pytest.mark.asyncio
async def test_config_neutralizada_limpia_aprueba(db: AsyncSession, market_data_service):
    # config totalmente neutralizada (lo que deja inventario_l4): sin filters/regime/
    # score_minimum → APPROVE, UNKNOWN.
    r = await FilterPipeline(market_data_service).evaluate(
        db, _signal(), _strategy(), {"mode": "normal"})
    assert r.outcome == "APPROVE" and r.quality == "UNKNOWN"


# ---------------------------------------------------------------------------
# 3) Régimen no bloquea (sin la llave regime, el gate ni corre)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_regimen_no_bloquea_sin_llave(db: AsyncSession, market_data_service):
    r = await FilterPipeline(market_data_service).evaluate(
        db, _signal(), _strategy(), {"mode": "normal"})
    assert r.block_reason != "regime_not_allowed" and r.outcome == "APPROVE"


# ---------------------------------------------------------------------------
# 4) Exits intactos — L4 se salta en salidas
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_exit_intacto_l4_saltado(db: AsyncSession, market_data_service):
    r = await FilterPipeline(market_data_service).evaluate(
        db, _signal(action="exit", sentiment="flat"), _strategy(), {"mode": "normal"})
    assert r.outcome == "APPROVE"
    assert r.block_reason != "score_below_minimum"
    ex = r.pipeline_execution_json.get("level_4") or {}
    assert ex.get("skipped") is True     # exit → N4 no corre


# ---------------------------------------------------------------------------
# 5) Alta — ninguna estrategia futura nace con la llave score_minimum
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_alta_no_persiste_score_minimum(client: AsyncClient, db: AsyncSession):
    # se envía score_minimum en el form (campo retirado): debe IGNORARSE
    resp = await client.post("/ui/strategies/new", data={
        "strategy_id": "filt_alta", "name": "Alta", "asset_symbol": "MES",
        "timeframe": "5m", "initial_mode": "paper", "score_minimum": "80"})
    assert resp.status_code == 303
    prof = (await db.execute(select(StrategyProfile).where(
        StrategyProfile.strategy_id == "filt_alta"))).scalar_one_or_none()
    # la estrategia nace SIN la llave score_minimum en su config
    pj = (prof.pipeline_config_json or {}) if prof else {}
    assert "score_minimum" not in pj
